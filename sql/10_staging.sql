-- =====================================================================
-- staging 계층: raw(전부 문자열)를 올바른 타입으로 바꾸고 정리한다.
--   1) 날짜 문자열 → DATETIME, 숫자 문자열 → DECIMAL/INT
--   2) 키 컬럼에 PRIMARY KEY 를 걸어 중복이 없음을 DB가 보장
--   3) 중복·누락 같은 원본 품질 이슈를 이 단계에서 처리
-- 파이프라인이 이 파일을 통째로 다시 실행하므로 DROP → CREATE 순서로 쓴다.
-- =====================================================================


-- 주문 (1행 = 주문 1건)
DROP TABLE IF EXISTS stg_orders;

CREATE TABLE stg_orders AS
SELECT
    order_id,
    customer_id,
    order_status,
    CAST(order_purchase_timestamp      AS DATETIME) AS purchased_at,
    CAST(order_approved_at             AS DATETIME) AS approved_at,
    CAST(order_delivered_carrier_date  AS DATETIME) AS delivered_carrier_at,
    CAST(order_delivered_customer_date AS DATETIME) AS delivered_customer_at,
    CAST(order_estimated_delivery_date AS DATETIME) AS estimated_delivery_at
FROM raw_orders;

ALTER TABLE stg_orders
    MODIFY order_id VARCHAR(32) NOT NULL,
    MODIFY customer_id VARCHAR(32) NOT NULL,
    MODIFY order_status VARCHAR(20) NOT NULL,
    ADD PRIMARY KEY (order_id),
    ADD INDEX ix_stg_orders_customer (customer_id);


-- 주문 상품 (1행 = 주문 안의 상품 1개)
DROP TABLE IF EXISTS stg_order_items;

CREATE TABLE stg_order_items AS
SELECT
    order_id,
    CAST(order_item_id AS UNSIGNED)          AS order_item_id,
    product_id,
    seller_id,
    CAST(shipping_limit_date AS DATETIME)    AS shipping_limit_at,
    CAST(price AS DECIMAL(10,2))             AS price,
    CAST(freight_value AS DECIMAL(10,2))     AS freight_value
FROM raw_order_items;

ALTER TABLE stg_order_items
    MODIFY order_id VARCHAR(32) NOT NULL,
    MODIFY product_id VARCHAR(32) NOT NULL,
    MODIFY seller_id VARCHAR(32) NOT NULL,
    ADD PRIMARY KEY (order_id, order_item_id),
    ADD INDEX ix_stg_items_seller (seller_id),
    ADD INDEX ix_stg_items_product (product_id);


-- 결제 (1행 = 주문의 결제 수단 1건, 한 주문에 여러 건 가능)
DROP TABLE IF EXISTS stg_order_payments;

CREATE TABLE stg_order_payments AS
SELECT
    order_id,
    CAST(payment_sequential AS UNSIGNED)     AS payment_sequential,
    payment_type,
    CAST(payment_installments AS UNSIGNED)   AS payment_installments,
    CAST(payment_value AS DECIMAL(10,2))     AS payment_value
FROM raw_order_payments;

ALTER TABLE stg_order_payments
    MODIFY order_id VARCHAR(32) NOT NULL,
    ADD PRIMARY KEY (order_id, payment_sequential);


-- 고객 (customer_id 는 주문마다 새로 발급, 실제 사람은 customer_unique_id)
DROP TABLE IF EXISTS stg_customers;

CREATE TABLE stg_customers AS
SELECT
    customer_id,
    customer_unique_id,
    customer_zip_code_prefix AS zip_code_prefix,
    customer_city            AS city,
    customer_state           AS state
FROM raw_customers;

ALTER TABLE stg_customers
    MODIFY customer_id VARCHAR(32) NOT NULL,
    MODIFY customer_unique_id VARCHAR(32) NOT NULL,
    MODIFY zip_code_prefix VARCHAR(5),
    ADD PRIMARY KEY (customer_id);


-- 셀러
DROP TABLE IF EXISTS stg_sellers;

CREATE TABLE stg_sellers AS
SELECT
    seller_id,
    seller_zip_code_prefix AS zip_code_prefix,
    seller_city            AS city,
    seller_state           AS state
FROM raw_sellers;

ALTER TABLE stg_sellers
    MODIFY seller_id VARCHAR(32) NOT NULL,
    MODIFY zip_code_prefix VARCHAR(5),
    ADD PRIMARY KEY (seller_id);


-- 상품: 포르투갈어 카테고리를 영어로 표준화
--   번역표에 없는 카테고리(13개 상품)는 원래 이름, 카테고리가 비어 있으면(610개) 'unknown'.
--   INNER JOIN 을 쓰면 이 623개 상품이 통째로 사라지므로 LEFT JOIN.
DROP TABLE IF EXISTS stg_products;

CREATE TABLE stg_products AS
SELECT
    p.product_id,
    p.product_category_name                                              AS category_pt,
    COALESCE(t.product_category_name_english, p.product_category_name, 'unknown') AS category,
    CAST(p.product_weight_g  AS UNSIGNED)                                AS weight_g,
    CAST(p.product_length_cm AS UNSIGNED)                                AS length_cm,
    CAST(p.product_height_cm AS UNSIGNED)                                AS height_cm,
    CAST(p.product_width_cm  AS UNSIGNED)                                AS width_cm,
    CAST(p.product_photos_qty AS UNSIGNED)                               AS photos_qty
FROM raw_products p
LEFT JOIN raw_category_translation t
       ON p.product_category_name = t.product_category_name;

ALTER TABLE stg_products
    MODIFY product_id VARCHAR(32) NOT NULL,
    MODIFY category VARCHAR(100) NOT NULL,
    MODIFY category_pt VARCHAR(100),
    ADD PRIMARY KEY (product_id);


-- 리뷰: 한 주문에 리뷰가 여러 개인 경우 리뷰 작성일 기준 가장 최근 1건만 남김
--   (1월 pandas 분석과 같은 규칙. 작성일이 같으면 답변 시각이 늦은 쪽)
DROP TABLE IF EXISTS stg_order_reviews;

CREATE TABLE stg_order_reviews AS
SELECT review_id, order_id, review_score, review_comment_title, review_comment_message,
       review_created_at, review_answered_at, reviews_per_order
FROM (
    SELECT
        review_id,
        order_id,
        CAST(review_score AS UNSIGNED)              AS review_score,
        NULLIF(TRIM(review_comment_title), '')      AS review_comment_title,
        NULLIF(TRIM(review_comment_message), '')    AS review_comment_message,
        CAST(review_creation_date    AS DATETIME)   AS review_created_at,
        CAST(review_answer_timestamp AS DATETIME)   AS review_answered_at,
        COUNT(*) OVER (PARTITION BY order_id)       AS reviews_per_order,
        ROW_NUMBER() OVER (
            PARTITION BY order_id
            ORDER BY CAST(review_creation_date AS DATETIME) DESC,
                     CAST(review_answer_timestamp AS DATETIME) DESC
        ) AS rn
    FROM raw_order_reviews
) ranked
WHERE rn = 1;

ALTER TABLE stg_order_reviews
    MODIFY order_id VARCHAR(32) NOT NULL,
    ADD PRIMARY KEY (order_id);


-- 우편번호 위치: 원본은 우편번호 앞 5자리 하나에 좌표가 여러 개(100만 행) → 평균 좌표 1개로 요약
DROP TABLE IF EXISTS stg_zip_locations;

CREATE TABLE stg_zip_locations AS
SELECT
    geolocation_zip_code_prefix              AS zip_code_prefix,
    AVG(CAST(geolocation_lat AS DECIMAL(12,8))) AS lat,
    AVG(CAST(geolocation_lng AS DECIMAL(12,8))) AS lng,
    COUNT(*)                                  AS source_points
FROM raw_geolocation
GROUP BY geolocation_zip_code_prefix;

ALTER TABLE stg_zip_locations
    MODIFY zip_code_prefix VARCHAR(5) NOT NULL,
    ADD PRIMARY KEY (zip_code_prefix);
