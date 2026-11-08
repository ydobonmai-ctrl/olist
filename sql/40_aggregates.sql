-- =====================================================================
-- 집계 테이블: 지표 뷰 결과를 파이프라인 실행 시점에 미리 계산해 저장한다.
-- 뷰는 조회할 때마다 다시 계산하므로(셀러 점수 약 1초, 전체 KPI 약 4초),
-- API·대시보드처럼 자주 조회하는 곳은 이 테이블을 읽는다.
-- =====================================================================

DROP TABLE IF EXISTS agg_kpi_overall;
CREATE TABLE agg_kpi_overall AS SELECT NOW() AS refreshed_at, v.* FROM v_kpi_overall v;

DROP TABLE IF EXISTS agg_kpi_monthly;
CREATE TABLE agg_kpi_monthly AS SELECT * FROM v_kpi_monthly;
ALTER TABLE agg_kpi_monthly ADD PRIMARY KEY (ym);

DROP TABLE IF EXISTS agg_category_kpi;
CREATE TABLE agg_category_kpi AS SELECT * FROM v_category_kpi;
ALTER TABLE agg_category_kpi ADD PRIMARY KEY (category);

DROP TABLE IF EXISTS agg_seller_health;
CREATE TABLE agg_seller_health AS SELECT * FROM v_seller_health;
ALTER TABLE agg_seller_health
    MODIFY seller_id VARCHAR(32) NOT NULL,
    ADD PRIMARY KEY (seller_id),
    ADD INDEX ix_agg_seller_grade (grade);
