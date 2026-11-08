# Olist Mini Data Warehouse

브라질 이커머스 Olist 공개 데이터(CSV 9개, 약 155만 행)를 MySQL에 적재하고,
분석용 스타 스키마·공통 지표·품질 검사·조회 API·리뷰 AI 분석까지 연결한 미니 데이터 웨어하우스.

## 데이터 출처와 라이선스

- 원본: [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) (Kaggle), **CC BY-NC-SA 4.0**
- 원본 CSV는 저장소에 넣지 않았습니다. 위 링크에서 받아 `.env`의 `OLIST_RAW_DIR`에 경로를 지정하세요.
- `labeling/sample_1500.*`은 원본 리뷰에서 무작위로 뽑은 1,500건(익명화된 공개 데이터)이고, `labeling/labels.txt`는 LLM(Claude)이 읽고 붙인 불만 유형 라벨입니다. 이 파생 데이터도 원본과 같은 CC BY-NC-SA 4.0 조건을 따릅니다(비상업적 이용, 출처 표기).

## 구조

```
CSV 9개
  → [Python] load_raw.py        raw_*   원본 그대로 적재 + CSV/DB 행 수 대조
  → [SQL] 10_staging.sql        stg_*   타입 변환, 기본키, 중복 리뷰 제거, 카테고리 표준화
  → [SQL] 20_star_schema.sql    fact_orders, fact_order_items, dim_date/customer/seller/product
  → [SQL] 30_metrics.sql        v_*     공통 지표 뷰 (KPI, 월별, 카테고리, 셀러 Health Score, 시나리오)
  → [SQL] 40_aggregates.sql     agg_*   API용 사전 집계 테이블
  → [Python] quality_checks.py  dq_results  품질 규칙 13개 (error 는 파이프라인 중단)
  → [Python] validate.py        2026.01 pandas 분석 결과와 수치 대조 → validation_report.md
  → [Python] customer_segments.py  RFM + K-Means 고객 세그먼트 (customer_unique_id 기준)
  → [Python] review_classifier.py  불만 리뷰 1,500건 LLM 라벨링 → TF-IDF+로지스틱 회귀 → 14,377건 분류
  → [Python] review_ai.py       (선택) OpenAI API 분류·임베딩 — 크레딧 필요, 현재 미사용
  → [FastAPI] api/main.py       /kpi, /categories, /sellers, /customers/segments, /reviews/complaints
  → [MCP] mcp_server/server.py  AI 에이전트(Claude)가 품질 검사·위반 건·지표를 도구로 호출
```

## MCP 서버: AI 에이전트용 검수 도구 (`mcp_server/`)

DW를 Claude 같은 AI 에이전트가 쓰는 도구로 노출합니다. AI가 DB에 아무 SQL이나 보내지 않고, 미리 정한 도구만 부르게 했습니다.

| 도구 | 하는 일 |
|---|---|
| `run_quality_checks` | 품질 규칙 13개 실행 → `dq_results`에 기록, error 위반 시 `passed=false` |
| `quality_history` | 최근 실행별 error·warn 위반 수 |
| `find_violations` | 규칙을 어긴 실제 건 샘플 (원인 확인·담당자 전달용) |
| `get_kpi` / `seller_health` / `complaint_breakdown` | 공통 지표 조회 |
| `run_readonly_sql` | 정해진 도구로 답이 안 될 때만: SELECT 한 문장, 200행, 10초, 변경 키워드 차단 + 세션 READ ONLY |

- 이 PC의 Python이 3.9라 공식 SDK(3.10+) 대신 MCP의 JSON-RPC(stdio) 중 `initialize`·`tools/list`·`tools/call`을 직접 구현했습니다.
- 품질 규칙은 `pipeline/quality_checks.py`와 같은 정의를 import해서 파이프라인과 AI가 같은 기준으로 검사합니다.
- 점검: `.venv\Scripts\python.exe mcp_server\smoke_test.py` (도구 호출, 쓰기·다중 문장 차단, 읽기 전용 뒤 쓰기 정상 여부)
- Claude Code 연결: 저장소의 `.mcp.json`이 서버를 등록합니다. 폴더에서 `claude` 실행 후
  "품질 검사 돌리고 결제액 불일치 건 3개와 담당자용 요약 써줘" → `run_quality_checks` → `find_violations` 순으로 호출해 답합니다.

## 실행

```powershell
.\.venv\Scripts\python.exe pipeline\run_pipeline.py              # 전체 (raw 적재 포함 약 4분)
.\.venv\Scripts\python.exe pipeline\run_pipeline.py --skip-raw   # SQL 단계부터
.\.venv\Scripts\python.exe pipeline\review_ai.py classify        # 불만 리뷰 분류 (OPENAI_API_KEY 필요)
.\.venv\Scripts\python.exe pipeline\review_ai.py embed           # 리뷰 임베딩
.\.venv\Scripts\python.exe -m uvicorn api.main:app --reload      # http://127.0.0.1:8000/docs
```

## 주요 결과

- 1월 pandas 분석과 대조: 전체 KPI·D등급 제외 시나리오·월별 23개월·셀러 등급 2,970명 모두 일치
- 차이 1건(의도한 개선): pandas 분석은 번역표에 없는 카테고리 2개를 'unknown'에 합쳤고, SQL은 분리
- 지연율 정의 차이 발견: 주문 기준 8.11% vs 상품 행 기준 7.91% → 지표 뷰에 두 정의를 이름으로 구분
- 품질 이슈: 고객 ID 99,441개 = 실제 96,096명, 번역 누락 상품 623개, 복수 리뷰 주문 547건,
  도착일 없는 배송 완료 주문 8건, 결제액 불일치 247건
- 고객 93,358명 중 97%가 1회 구매. 재구매 고객(LOYAL) 3.0%·매출 5.6%
- 첫 주문이 지연된 고객의 재구매율 3.14% vs 정시 4.05% (약 22% 낮음, z≈2.9)
- 리뷰 분류 모델: 5-fold 정확도 0.751, macro F1 0.703 (review_model_report.md)
- 불만의 28.5%가 '미배송'(실제 지연 52.6%), 상품 문제(오배송·품질·파손) 47%는 실제 지연 5~6% → 물류보다 상품·셀러 이행 문제
