# 검증 보고서 (2026-11-08 15:23)

기준: 2026.01 pandas 분석 결과 CSV / 비교 대상: MySQL 지표 뷰

## 1. 전체 KPI와 D등급 제외 시나리오

| 시나리오 | 지표 | pandas | SQL | 결과 |
|---|---|---:|---:|---|
| All_sellers | revenue | 15,419,773.750000 | 15,419,773.750000 | 일치 |
| All_sellers | orders | 96,478.000000 | 96,478.000000 | 일치 |
| All_sellers | late_ratio | 0.079077 | 0.079077 | 일치 |
| All_sellers | avg_review | 4.081649 | 4.081768 | 일치 |
| Without_D | revenue | 14,138,953.400000 | 14,138,953.400000 | 일치 |
| Without_D | orders | 91,280.000000 | 91,280.000000 | 일치 |
| Without_D | late_ratio | 0.072082 | 0.072082 | 일치 |
| Without_D | avg_review | 4.131191 | 4.131318 | 일치 |

## 2. 월별 KPI

- 월 수: pandas 23 / SQL 23
- 매출 일치 23/23, 주문 수 일치 23/23, 지연율 일치 23/23

## 3. 카테고리 KPI

- 카테고리 수: pandas 72 / SQL 74, 공통 72
- 공통 카테고리 중 매출 불일치: ['unknown']
- SQL에만 있음: ['pc_gamer', 'portateis_cozinha_e_preparadores_de_alimentos'] / pandas에만 있음: 없음
- 원인: pandas 분석은 번역표에 없는 카테고리(pc_gamer 등)를 'unknown'에 합쳤고, SQL은 원래 이름을 유지해 별도 카테고리로 분리함 (의도한 개선)

## 4. 셀러 Health Score와 등급

- 셀러 수: pandas 2970 / SQL 2970
- health_score 최대 오차: 4.04e-03 (오차 1e-6 이하 2958/2970)
- 등급 일치: 2970/2970
- SQL 등급 분포: {'A': 1042, 'B': 443, 'C': 742, 'D': 743}

## 결론

핵심 지표 모두 일치
