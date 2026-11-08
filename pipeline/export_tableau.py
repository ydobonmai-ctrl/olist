"""Tableau Public 대시보드용 데이터 추출 → tableau/olist_delivery_orders.csv

1행 = 배송 완료 주문 1건 (96,478행). 대시보드의 모든 시트가 이 파일 하나로 만들어지도록
주문·고객 지역·셀러 등급·카테고리·리뷰 불만 유형을 한 줄에 붙인다.
- 셀러·카테고리: 주문의 첫 번째 상품 기준 (주문의 98% 이상은 셀러 1명)
- is_late: 실제 도착 > 예상 도착이면 1 (도착일 없는 8건은 0)
"""
import pandas as pd

from config import ROOT, get_engine

STATES = {
    "AC": "Acre", "AL": "Alagoas", "AP": "Amapá", "AM": "Amazonas", "BA": "Bahia", "CE": "Ceará",
    "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MT": "Mato Grosso",
    "MS": "Mato Grosso do Sul", "MG": "Minas Gerais", "PA": "Pará", "PB": "Paraíba", "PR": "Paraná",
    "PE": "Pernambuco", "PI": "Piauí", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte",
    "RS": "Rio Grande do Sul", "RO": "Rondônia", "RR": "Roraima", "SC": "Santa Catarina",
    "SP": "São Paulo", "SE": "Sergipe", "TO": "Tocantins",
}

COMPLAINT_KO = {
    "not_received": "미배송", "wrong_or_missing_item": "오배송·구성 누락", "quality_mismatch": "품질·설명 불일치",
    "damaged_defective": "파손·불량", "delivery_delay": "배송 지연", "seller_service": "판매자 응대",
    "positive_or_neutral": "불만 아님", "other": "기타",
}

SQL = """
WITH first_item AS (
    SELECT order_id, seller_id, product_id
    FROM fact_order_items WHERE order_item_id = 1
)
SELECT
    o.order_id,
    DATE(o.purchased_at)                         AS purchase_date,
    d.ym                                         AS purchase_month,
    c.state                                      AS customer_state,
    s.state                                      AS seller_state,
    (c.state = s.state)                          AS same_state,
    p.category,
    h.grade                                      AS seller_grade,
    COALESCE(o.is_late, 0)                       AS is_late,
    o.delivery_days,
    o.days_vs_estimate,
    o.review_score,
    o.items_price + o.items_freight              AS order_revenue,
    o.items_freight                              AS freight,
    l.complaint_type
FROM fact_orders o
JOIN dim_date d       ON d.date_key = o.purchase_date_key
JOIN dim_customer c   ON c.customer_id = o.customer_id
JOIN first_item fi    ON fi.order_id = o.order_id
JOIN dim_seller s     ON s.seller_id = fi.seller_id
JOIN dim_product p    ON p.product_id = fi.product_id
LEFT JOIN agg_seller_health h ON h.seller_id = fi.seller_id
LEFT JOIN review_labels l     ON l.order_id = o.order_id
WHERE o.order_status = 'delivered'
"""


def main():
    df = pd.read_sql(SQL, get_engine())
    df.insert(df.columns.get_loc("customer_state") + 1, "customer_state_name", df["customer_state"].map(STATES))
    df.insert(df.columns.get_loc("customer_state_name") + 1, "country", "Brazil")
    df["complaint_type"] = df["complaint_type"].map(COMPLAINT_KO)
    df["same_state"] = df["same_state"].map({1: "같은 주", 0: "다른 주"})
    out = ROOT / "tableau"
    out.mkdir(exist_ok=True)
    path = out / "olist_delivery_orders.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")

    print(f"{path} : {len(df):,}행, {path.stat().st_size / 1e6:.1f}MB")
    print(f"지연율(주문 기준) {df.is_late.mean():.4f} / 주 {df.customer_state.nunique()}개 / "
          f"셀러 등급 결측 {df.seller_grade.isna().sum()} / 불만 분류 {df.complaint_type.notna().sum():,}건")


if __name__ == "__main__":
    main()
