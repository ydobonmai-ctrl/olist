"""데이터 품질 검사: 파이프라인이 끝날 때마다 실행해 결과를 dq_results 테이블에 쌓는다.

각 검사는 "규칙을 어긴 행 수"를 세는 SQL 하나다.
  error : 0이 아니면 파이프라인을 실패로 처리 (분석에 쓰면 안 되는 상태)
  warn  : 기록만 하고 진행 (알고 쓰면 되는 원본 특성)
"""
from datetime import datetime

from sqlalchemy import text

from config import get_engine

CHECKS = [
    # (이름, 심각도, 위반 행 수를 세는 SQL)
    ("raw_orders 와 stg_orders 행 수 일치", "error",
     "SELECT ABS((SELECT COUNT(*) FROM raw_orders) - (SELECT COUNT(*) FROM stg_orders))"),
    ("fact_order_items 상품 키가 dim_product 에 존재", "error",
     "SELECT COUNT(*) FROM fact_order_items f LEFT JOIN dim_product d ON f.product_id = d.product_id WHERE d.product_id IS NULL"),
    ("fact_order_items 셀러 키가 dim_seller 에 존재", "error",
     "SELECT COUNT(*) FROM fact_order_items f LEFT JOIN dim_seller d ON f.seller_id = d.seller_id WHERE d.seller_id IS NULL"),
    ("fact_orders 고객 키가 dim_customer 에 존재", "error",
     "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_customer d ON f.customer_id = d.customer_id WHERE d.customer_id IS NULL"),
    ("fact 날짜 키가 dim_date 범위 안에 존재", "error",
     "SELECT COUNT(*) FROM fact_orders f LEFT JOIN dim_date d ON f.purchase_date_key = d.date_key WHERE d.date_key IS NULL"),
    ("상품가·배송비 음수 없음", "error",
     "SELECT COUNT(*) FROM fact_order_items WHERE price < 0 OR freight_value < 0"),
    ("리뷰 점수 1~5 범위", "error",
     "SELECT COUNT(*) FROM fact_orders WHERE review_score IS NOT NULL AND review_score NOT BETWEEN 1 AND 5"),
    ("주문당 리뷰 1건 (중복 제거 확인)", "error",
     "SELECT COUNT(*) FROM (SELECT order_id FROM stg_order_reviews GROUP BY order_id HAVING COUNT(*) > 1) x"),
    ("배송 완료인데 도착일 없음", "warn",
     "SELECT COUNT(*) FROM fact_orders WHERE order_status = 'delivered' AND delivered_customer_at IS NULL"),
    ("도착일이 구매일보다 빠름", "warn",
     "SELECT COUNT(*) FROM fact_orders WHERE delivered_customer_at < purchased_at"),
    ("결제액과 상품가+배송비 차이 1 BRL 초과 (배송 완료)", "warn",
     "SELECT COUNT(*) FROM fact_orders WHERE order_status = 'delivered' AND ABS(payment_value - items_price - items_freight) > 1"),
    ("카테고리 미분류 상품", "warn",
     "SELECT COUNT(*) FROM dim_product WHERE category = 'unknown'"),
    ("위치 좌표 없는 고객", "warn",
     "SELECT COUNT(*) FROM dim_customer WHERE lat IS NULL"),
]


def main():
    engine = get_engine()
    run_at = datetime.now().replace(microsecond=0)
    results = []
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS dq_results (
                run_at     DATETIME     NOT NULL,
                check_name VARCHAR(100) NOT NULL,
                severity   VARCHAR(5)   NOT NULL,
                violations INT          NOT NULL,
                passed     TINYINT      NOT NULL,
                PRIMARY KEY (run_at, check_name)
            )"""))
        for name, severity, sql in CHECKS:
            violations = int(conn.execute(text(sql)).scalar() or 0)
            passed = violations == 0
            results.append((name, severity, violations, passed))
            conn.execute(text("INSERT INTO dq_results VALUES (:r, :n, :s, :v, :p)"),
                         {"r": run_at, "n": name, "s": severity, "v": violations, "p": passed})

    for name, severity, violations, passed in results:
        mark = "PASS" if passed else ("FAIL" if severity == "error" else "WARN")
        print(f"[{mark}] {name}: {violations:,}")
    failed = [r for r in results if r[1] == "error" and not r[3]]
    if failed:
        raise SystemExit(f"품질 검사 실패 {len(failed)}건 — 분석에 쓰기 전에 원인을 확인하세요.")
    print("품질 검사 통과 (warn 은 기록만)")


if __name__ == "__main__":
    main()
