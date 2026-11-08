"""지표 조회 API: 분석가·서비스가 DB에 직접 붙지 않고 공통 지표를 HTTP로 받는다.

    .venv\\Scripts\\python.exe -m uvicorn api.main:app --reload
    → http://127.0.0.1:8000/docs 에서 모든 엔드포인트를 바로 시험할 수 있다.
"""
import sys
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from config import get_engine  # noqa: E402

app = FastAPI(title="Olist Mini DW API", version="1.0",
              description="MySQL 미니 데이터 웨어하우스의 공통 지표 조회 API")
engine = get_engine()

CATEGORY_SORT = {"revenue", "orders", "avg_review", "late_ratio_item", "revenue_share"}


def rows(sql, **params):
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


@app.get("/health", summary="API·DB 연결 확인")
def health():
    rows("SELECT 1")
    return {"status": "ok"}


@app.get("/kpi/overall", summary="전체 KPI (매출, 주문 수, 지연율 두 가지 정의 등)")
def kpi_overall():
    return rows("SELECT * FROM agg_kpi_overall")[0]


@app.get("/kpi/monthly", summary="월별 KPI")
def kpi_monthly(start: str = Query("2016-01", pattern=r"^\d{4}-\d{2}$", description="시작 월 YYYY-MM"),
                end: str = Query("2099-12", pattern=r"^\d{4}-\d{2}$", description="끝 월 YYYY-MM")):
    return rows("SELECT * FROM agg_kpi_monthly WHERE ym BETWEEN :s AND :e ORDER BY ym", s=start, e=end)


@app.get("/categories", summary="카테고리별 KPI")
def categories(sort: str = Query("revenue", description=f"정렬 기준: {', '.join(sorted(CATEGORY_SORT))}"),
               limit: int = Query(10, ge=1, le=100)):
    if sort not in CATEGORY_SORT:
        raise HTTPException(400, f"sort 는 {sorted(CATEGORY_SORT)} 중 하나")
    # 정렬 컬럼은 화이트리스트로 검증한 값만 SQL 에 넣는다 (SQL 인젝션 방지)
    return rows(f"SELECT * FROM agg_category_kpi ORDER BY {sort} DESC LIMIT :n", n=limit)


@app.get("/sellers", summary="셀러 목록 (등급 필터)")
def sellers(grade: Optional[str] = Query(None, pattern="^[ABCD]$"),
            limit: int = Query(20, ge=1, le=500)):
    if grade:
        return rows("SELECT * FROM agg_seller_health WHERE grade = :g ORDER BY health_score LIMIT :n",
                    g=grade, n=limit)
    return rows("SELECT * FROM agg_seller_health ORDER BY health_score DESC LIMIT :n", n=limit)


@app.get("/customers/segments", summary="RFM + K-Means 고객 세그먼트 요약")
def customer_segments():
    return rows("SELECT * FROM agg_customer_segments ORDER BY avg_ltv DESC")


@app.get("/reviews/complaints", summary="불만 유형별 리뷰 수와 실제 배송 지연 비율 (텍스트 분류 모델 결과)")
def complaints():
    # LLM 이 텍스트로 판단한 불만 유형을, 주문 데이터의 실제 지연 여부와 교차 확인한다
    return rows("""
        SELECT l.complaint_type,
               COUNT(*)                                    AS reviews,
               COUNT(*) / SUM(COUNT(*)) OVER ()            AS share,
               AVG(l.review_score)                         AS avg_score,
               AVG(COALESCE(o.is_late, 0))                 AS actual_late_ratio,
               AVG(o.days_vs_estimate)                     AS avg_days_vs_estimate
        FROM review_labels l
        JOIN fact_orders o ON o.order_id = l.order_id
        GROUP BY l.complaint_type
        ORDER BY reviews DESC""")


@app.get("/sellers/{seller_id}", summary="셀러 1명의 Health Score와 등급")
def seller(seller_id: str):
    found = rows("SELECT * FROM agg_seller_health WHERE seller_id = :id", id=seller_id)
    if not found:
        raise HTTPException(404, "셀러를 찾을 수 없음 (배송 완료 주문이 없는 셀러는 점수 없음)")
    return found[0]
