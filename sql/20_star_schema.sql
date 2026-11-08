-- =====================================================================
-- mart 계층: 분석용 스타 스키마
--
--   fact_orders       1행 = 주문 1건    (배송·리뷰·결제 등 주문 단위 사실)
--   fact_order_items  1행 = 주문 상품 1개 (매출 단위 사실)
--   dim_date / dim_customer / dim_seller / dim_product
--
-- 팩트는 "숫자로 쌓이는 사건", 디멘션은 "그 사건을 설명하는 기준"이다.
-- 분석가는 팩트에 필요한 디멘션만 붙여 집계한다.
-- =====================================================================


-- ---------------------------------------------------------------------
-- dim_date: 날짜 디멘션 (주문 기간 전체를 하루 1행으로 생성)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS dim_date;

CREATE TABLE dim_date (
    date_key     INT         NOT NULL PRIMARY KEY,   -- 20170315 형태
    full_date    DATE        NOT NULL,
    year         SMALLINT    NOT NULL,
    quarter      TINYINT     NOT NULL,
    month        TINYINT     NOT NULL,
    ym           CHAR(7)     NOT NULL,               -- 연-월 (year_month 는 MySQL 예약어라 ym)
    day_of_week  TINYINT     NOT NULL,               -- 1=일 ... 7=토
    is_weekend   TINYINT     NOT NULL
);

INSERT INTO dim_date
WITH RECURSIVE d AS (
    SELECT DATE('2016-09-01') AS dt
    UNION ALL
    SELECT dt + INTERVAL 1 DAY FROM d WHERE dt < '2018-12-31'
)
SELECT
    CAST(DATE_FORMAT(dt, '%Y%m%d') AS UNSIGNED),
    dt,
    YEAR(dt),
    QUARTER(dt),
    MONTH(dt),
    DATE_FORMAT(dt, '%Y-%m'),
    DAYOFWEEK(dt),
    DAYOFWEEK(dt) IN (1, 7)
FROM d;


-- ---------------------------------------------------------------------
-- dim_customer / dim_seller: 위치 좌표는 우편번호 요약 테이블에서 붙인다
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS dim_customer;

CREATE TABLE dim_customer AS
SELECT
    c.customer_id,
    c.customer_unique_id,
    c.zip_code_prefix,
    c.city,
    c.state,
    z.lat,
    z.lng
FROM stg_customers c
LEFT JOIN stg_zip_locations z ON c.zip_code_prefix = z.zip_code_prefix;

ALTER TABLE dim_customer
    ADD PRIMARY KEY (customer_id),
    ADD INDEX ix_dim_customer_unique (customer_unique_id);


DROP TABLE IF EXISTS dim_seller;

CREATE TABLE dim_seller AS
SELECT
    s.seller_id,
    s.zip_code_prefix,
    s.city,
    s.state,
    z.lat,
    z.lng
FROM stg_sellers s
LEFT JOIN stg_zip_locations z ON s.zip_code_prefix = z.zip_code_prefix;

ALTER TABLE dim_seller ADD PRIMARY KEY (seller_id);


-- ---------------------------------------------------------------------
-- dim_product
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS dim_product;

CREATE TABLE dim_product AS
SELECT product_id, category, category_pt, weight_g, length_cm, height_cm, width_cm, photos_qty
FROM stg_products;

ALTER TABLE dim_product
    ADD PRIMARY KEY (product_id),
    ADD INDEX ix_dim_product_category (category);


-- ---------------------------------------------------------------------
-- fact_orders: 주문 단위 사실
--   is_late       : 실제 도착 > 예상 도착 이면 1. 도착일이 없으면 NULL
--   delivery_days : 구매 → 고객 도착까지 걸린 일수
--   review_score  : 주문당 최신 리뷰 점수 (staging 에서 중복 제거)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS fact_orders;

CREATE TABLE fact_orders AS
SELECT
    o.order_id,
    o.customer_id,
    CAST(DATE_FORMAT(o.purchased_at, '%Y%m%d') AS UNSIGNED)   AS purchase_date_key,
    o.order_status,
    o.purchased_at,
    o.delivered_customer_at,
    o.estimated_delivery_at,
    o.order_status = 'delivered'                              AS is_delivered,
    CASE WHEN o.delivered_customer_at IS NULL THEN NULL
         ELSE o.delivered_customer_at > o.estimated_delivery_at END AS is_late,
    DATEDIFF(o.delivered_customer_at, o.purchased_at)        AS delivery_days,
    DATEDIFF(o.delivered_customer_at, o.estimated_delivery_at) AS days_vs_estimate,
    COALESCE(i.items_count, 0)                                AS items_count,
    COALESCE(i.items_price, 0)                                AS items_price,
    COALESCE(i.items_freight, 0)                              AS items_freight,
    COALESCE(p.payment_value, 0)                              AS payment_value,
    r.review_score
FROM stg_orders o
LEFT JOIN (
    SELECT order_id, COUNT(*) AS items_count, SUM(price) AS items_price, SUM(freight_value) AS items_freight
    FROM stg_order_items GROUP BY order_id
) i ON o.order_id = i.order_id
LEFT JOIN (
    SELECT order_id, SUM(payment_value) AS payment_value
    FROM stg_order_payments GROUP BY order_id
) p ON o.order_id = p.order_id
LEFT JOIN stg_order_reviews r ON o.order_id = r.order_id;

ALTER TABLE fact_orders
    ADD PRIMARY KEY (order_id),
    ADD INDEX ix_fact_orders_date (purchase_date_key),
    ADD INDEX ix_fact_orders_customer (customer_id);


-- ---------------------------------------------------------------------
-- fact_order_items: 주문 상품 단위 사실 (매출 = 상품가 + 배송비)
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS fact_order_items;

CREATE TABLE fact_order_items AS
SELECT
    i.order_id,
    i.order_item_id,
    i.product_id,
    i.seller_id,
    o.customer_id,
    CAST(DATE_FORMAT(o.purchased_at, '%Y%m%d') AS UNSIGNED)  AS purchase_date_key,
    i.price,
    i.freight_value,
    i.price + i.freight_value                                AS item_revenue
FROM stg_order_items i
JOIN stg_orders o ON i.order_id = o.order_id;

ALTER TABLE fact_order_items
    ADD PRIMARY KEY (order_id, order_item_id),
    ADD INDEX ix_items_seller (seller_id),
    ADD INDEX ix_items_product (product_id),
    ADD INDEX ix_items_date (purchase_date_key);
