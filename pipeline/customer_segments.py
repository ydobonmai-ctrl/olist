"""RFM + K-Means 고객 세그먼트 → MySQL customer_segments 테이블.

- 고객 단위: customer_unique_id (customer_id 는 주문마다 새로 발급되므로 사용하지 않음)
- 대상: 배송 완료 주문
- R: 기준일(마지막 구매일 다음 날) − 고객의 마지막 구매일 (일)
- F: 주문 수
- M: 상품가 + 배송비 합계 (BRL)
- 분포가 한쪽으로 치우쳐 있어 log1p 변환 후 표준화하고 K-Means(k=4, random_state=42)
"""
import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy import text

from config import get_engine

K = 4

RFM_SQL = """
SELECT c.customer_unique_id,
       COUNT(*)                                           AS frequency,
       MAX(o.purchased_at)                                AS last_purchase_at,
       SUM(o.items_price + o.items_freight)               AS monetary,
       AVG(o.review_score)                                AS avg_review,
       AVG(COALESCE(o.is_late, 0))                        AS late_ratio,
       SUM(COALESCE(o.is_late, 0))                        AS late_orders
FROM fact_orders o
JOIN dim_customer c ON c.customer_id = o.customer_id
WHERE o.order_status = 'delivered'
GROUP BY c.customer_unique_id
"""


def name_segments(centers):
    """군집 중심(R, F, M 원래 단위)으로 이름을 붙인다.
    - F(주문 수)가 가장 큰 군집          → LOYAL    재구매 고객
    - 나머지 중 R(경과일)이 가장 작은 군집 → RECENT   최근 첫 구매 고객
    - 남은 두 군집 중 M이 큰 쪽           → AT_RISK  고액이지만 오래 재구매 없음
    - 남은 군집                           → DORMANT  저액·오래 재구매 없음
    """
    names = {}
    loyal = centers["frequency"].idxmax()
    names[loyal] = "LOYAL"
    rest = centers.drop(index=loyal)
    recent = rest["recency"].idxmin()
    names[recent] = "RECENT"
    rest = rest.drop(index=recent)
    names[rest["monetary"].idxmax()] = "AT_RISK"
    names[rest["monetary"].idxmin()] = "DORMANT"
    return names


def main(explore=False):
    engine = get_engine()
    df = pd.read_sql(RFM_SQL, engine, parse_dates=["last_purchase_at"])
    ref = df["last_purchase_at"].max().normalize() + pd.Timedelta(days=1)
    df["recency"] = (ref - df["last_purchase_at"]).dt.days

    X = StandardScaler().fit_transform(np.log1p(df[["recency", "frequency", "monetary"]].astype(float)))

    if explore:
        sample = np.random.default_rng(42).choice(len(X), 10000, replace=False)
        for k in range(3, 7):
            labels = KMeans(n_clusters=k, n_init=10, random_state=42).fit_predict(X)
            print(f"k={k} silhouette={silhouette_score(X[sample], labels[sample]):.3f}")

    model = KMeans(n_clusters=K, n_init=10, random_state=42).fit(X)
    df["cluster"] = model.labels_
    centers = df.groupby("cluster")[["recency", "frequency", "monetary"]].mean()
    names = name_segments(centers)
    df["segment"] = df["cluster"].map(names)

    total_rev = df["monetary"].sum()
    summary = (df.groupby("segment")
                 .agg(customers=("customer_unique_id", "count"),
                      revenue=("monetary", "sum"),
                      avg_ltv=("monetary", "mean"),
                      avg_recency=("recency", "mean"),
                      avg_frequency=("frequency", "mean"),
                      avg_review=("avg_review", "mean"),
                      late_orders=("late_orders", "sum"),
                      orders=("frequency", "sum"))
                 .reset_index())
    summary["customer_share"] = summary["customers"] / summary["customers"].sum()
    summary["revenue_share"] = summary["revenue"] / total_rev
    summary["late_ratio_order"] = summary["late_orders"] / summary["orders"]
    summary = summary.sort_values("avg_ltv", ascending=False)

    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS customer_segments"))
    df[["customer_unique_id", "recency", "frequency", "monetary", "avg_review", "late_ratio", "cluster", "segment"]] \
        .to_sql("customer_segments", engine, index=False, chunksize=5000, method="multi")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE customer_segments MODIFY customer_unique_id VARCHAR(32) NOT NULL, "
                          "MODIFY segment VARCHAR(20) NOT NULL, ADD PRIMARY KEY (customer_unique_id), ADD INDEX ix_seg (segment)"))
        conn.execute(text("DROP TABLE IF EXISTS agg_customer_segments"))
    summary.to_sql("agg_customer_segments", engine, index=False)

    pd.set_option("display.width", 200)
    print(f"기준일 {ref.date()}, 고객 {len(df):,}명, k={K}")
    print(summary[["segment", "customers", "customer_share", "revenue_share", "avg_ltv", "avg_recency",
                   "avg_frequency", "avg_review", "late_ratio_order"]].round(3).to_string(index=False))
    return summary


if __name__ == "__main__":
    import sys
    main(explore="--explore" in sys.argv)
