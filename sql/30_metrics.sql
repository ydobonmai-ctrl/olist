-- =====================================================================
-- 공통 지표 뷰: 지표 정의를 SQL 한 곳에 고정해 누구나 같은 숫자를 쓰게 한다.
--
-- [지연율 정의 두 가지]
--   late_ratio_order : 지연 주문 수 / 배송 완료 주문 수           ← 표준 지표로 권장
--   late_ratio_item  : 주문 상품 행 기준 평균 (상품이 많은 주문이 여러 번 셈)
--                      2026.01 pandas 분석이 사용한 방식. 수치 대조용으로 유지
-- [공통 규칙]
--   - 대상: order_status = 'delivered'
--   - 매출 = 상품가 + 배송비
--   - 리뷰 = 주문당 최신 1건
--   - 도착일이 비어 있는 배송 완료 주문(8건)은 지연 아님(0)으로 처리 (pandas 비교 결과와 동일)
-- =====================================================================


-- 배송 완료 주문의 상품 행 (대부분 지표의 기반)
CREATE OR REPLACE VIEW v_delivered_items AS
SELECT
    i.order_id,
    i.order_item_id,
    i.seller_id,
    i.product_id,
    p.category,
    d.ym,
    i.item_revenue,
    COALESCE(o.is_late, 0)  AS is_late,
    o.review_score
FROM fact_order_items i
JOIN fact_orders o ON o.order_id = i.order_id AND o.order_status = 'delivered'
JOIN dim_product p ON p.product_id = i.product_id
JOIN dim_date    d ON d.date_key   = i.purchase_date_key;


-- 배송 완료 주문 (주문 기준 지표용)
CREATE OR REPLACE VIEW v_delivered_orders AS
SELECT
    o.order_id,
    o.customer_id,
    d.ym,
    COALESCE(o.is_late, 0) AS is_late,
    o.delivery_days,
    o.days_vs_estimate,
    o.review_score,
    o.items_price + o.items_freight AS order_revenue
FROM fact_orders o
JOIN dim_date d ON d.date_key = o.purchase_date_key
WHERE o.order_status = 'delivered';


-- 전체 KPI
CREATE OR REPLACE VIEW v_kpi_overall AS
SELECT
    (SELECT SUM(item_revenue) FROM v_delivered_items)                         AS revenue,
    (SELECT COUNT(*) FROM v_delivered_orders)                                 AS orders,
    (SELECT SUM(item_revenue) FROM v_delivered_items)
      / (SELECT COUNT(*) FROM v_delivered_orders)                             AS avg_order_value,
    (SELECT AVG(CAST(review_score AS DOUBLE)) FROM v_delivered_orders)        AS avg_review_order,
    (SELECT AVG(CAST(review_score AS DOUBLE)) FROM v_delivered_items)         AS avg_review_item,
    (SELECT AVG(CAST(is_late AS DOUBLE)) FROM v_delivered_orders)             AS late_ratio_order,
    (SELECT AVG(CAST(is_late AS DOUBLE)) FROM v_delivered_items)              AS late_ratio_item,
    (SELECT AVG(delivery_days) FROM v_delivered_orders)                       AS avg_delivery_days;


-- 월별 KPI
CREATE OR REPLACE VIEW v_kpi_monthly AS
SELECT
    i.ym,
    SUM(i.item_revenue)                          AS revenue,
    COUNT(DISTINCT i.order_id)                   AS orders,
    AVG(CAST(i.review_score AS DOUBLE))          AS avg_review_item,
    AVG(CAST(i.is_late AS DOUBLE))               AS late_ratio_item,
    o.late_ratio_order
FROM v_delivered_items i
JOIN (
    SELECT ym, AVG(CAST(is_late AS DOUBLE)) AS late_ratio_order
    FROM v_delivered_orders GROUP BY ym
) o ON o.ym = i.ym
GROUP BY i.ym, o.late_ratio_order;


-- 카테고리별 KPI
CREATE OR REPLACE VIEW v_category_kpi AS
SELECT
    category,
    SUM(item_revenue)                                         AS revenue,
    COUNT(DISTINCT order_id)                                  AS orders,
    COUNT(review_score)                                       AS review_count,
    AVG(CAST(review_score AS DOUBLE))                         AS avg_review,
    AVG(CAST(is_late AS DOUBLE))                              AS late_ratio_item,
    SUM(item_revenue) / SUM(SUM(item_revenue)) OVER ()        AS revenue_share
FROM v_delivered_items
GROUP BY category;


-- 셀러 Health Score = 0.5 × 리뷰/5 + 0.3 × (1 − 지연율) + 0.2 × ln(1+리뷰 수)/최댓값
--   리뷰가 하나도 없는 셀러는 평균 3.5점으로 간주(보수적 페널티)
--   등급: health_score 의 25/50/75% 분위수(선형 보간, pandas quantile 과 같은 방식)로 A~D
CREATE OR REPLACE VIEW v_seller_health AS
WITH seller AS (
    SELECT
        seller_id,
        SUM(item_revenue)                                        AS revenue,
        COUNT(DISTINCT order_id)                                 AS orders,
        COUNT(review_score)                                      AS review_count,
        COALESCE(AVG(CAST(review_score AS DOUBLE)), 3.5)         AS avg_review,
        AVG(CAST(is_late AS DOUBLE))                             AS late_ratio
    FROM v_delivered_items
    GROUP BY seller_id
),
scored AS (
    SELECT
        s.*,
        s.avg_review / 5.0                                       AS score_review,
        1 - LEAST(GREATEST(s.late_ratio, 0), 1)                  AS score_otd,
        LN(1 + s.review_count) / MAX(LN(1 + s.review_count)) OVER () AS score_volume
    FROM seller s
),
health AS (
    SELECT *, 0.5 * score_review + 0.3 * score_otd + 0.2 * score_volume AS health_score
    FROM scored
),
ranked AS (
    SELECT health_score,
           ROW_NUMBER() OVER (ORDER BY health_score) - 1 AS idx,
           COUNT(*) OVER ()                               AS n
    FROM health
),
quantiles AS (
    SELECT
        q.p,
        MAX(CASE WHEN r.idx = FLOOR((r.n - 1) * q.p)     THEN r.health_score END) AS v_lo,
        MAX(CASE WHEN r.idx = FLOOR((r.n - 1) * q.p) + 1 THEN r.health_score END) AS v_hi,
        MAX((r.n - 1) * q.p - FLOOR((r.n - 1) * q.p))                              AS frac
    FROM ranked r
    CROSS JOIN (SELECT 0.25 AS p UNION ALL SELECT 0.50 UNION ALL SELECT 0.75) q
    GROUP BY q.p
),
cut AS (
    SELECT
        MAX(CASE WHEN p = 0.25 THEN v_lo + frac * (COALESCE(v_hi, v_lo) - v_lo) END) AS q25,
        MAX(CASE WHEN p = 0.50 THEN v_lo + frac * (COALESCE(v_hi, v_lo) - v_lo) END) AS q50,
        MAX(CASE WHEN p = 0.75 THEN v_lo + frac * (COALESCE(v_hi, v_lo) - v_lo) END) AS q75
    FROM quantiles
)
SELECT
    h.seller_id, h.revenue, h.orders, h.review_count, h.avg_review, h.late_ratio,
    h.score_review, h.score_otd, h.score_volume, h.health_score,
    CASE WHEN h.health_score >= c.q75 THEN 'A'
         WHEN h.health_score >= c.q50 THEN 'B'
         WHEN h.health_score >= c.q25 THEN 'C'
         ELSE 'D' END AS grade
FROM health h CROSS JOIN cut c;


-- 시나리오: D등급 셀러를 제외하면 KPI가 어떻게 바뀌는가
CREATE OR REPLACE VIEW v_scenario_without_d AS
WITH items AS (
    SELECT i.*, h.grade
    FROM v_delivered_items i
    JOIN v_seller_health h ON h.seller_id = i.seller_id
)
SELECT 'All_sellers' AS scenario,
       SUM(item_revenue) AS revenue, COUNT(DISTINCT order_id) AS orders,
       AVG(CAST(review_score AS DOUBLE)) AS avg_review_item, AVG(CAST(is_late AS DOUBLE)) AS late_ratio_item
FROM items
UNION ALL
SELECT 'Without_D',
       SUM(item_revenue), COUNT(DISTINCT order_id),
       AVG(CAST(review_score AS DOUBLE)), AVG(CAST(is_late AS DOUBLE))
FROM items WHERE grade <> 'D';
