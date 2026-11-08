"""1단계: 원본 CSV를 MySQL raw 테이블로 그대로 적재한다.

raw 계층은 원본을 손대지 않고 보관하는 곳이다. 모든 값을 문자열로 읽어
우편번호 앞자리 0 같은 정보가 사라지지 않게 하고, 타입 변환은 다음 단계(SQL)에서 한다.
"""
import time

import pandas as pd
from sqlalchemy import text

from config import RAW_DIR, get_engine

# 원본 파일 → raw 테이블 이름
SOURCES = {
    "olist_orders_dataset.csv": "raw_orders",
    "olist_order_items_dataset.csv": "raw_order_items",
    "olist_order_payments_dataset.csv": "raw_order_payments",
    "olist_order_reviews_dataset.csv": "raw_order_reviews",
    "olist_customers_dataset.csv": "raw_customers",
    "olist_sellers_dataset.csv": "raw_sellers",
    "olist_products_dataset.csv": "raw_products",
    "olist_geolocation_dataset.csv": "raw_geolocation",
    "product_category_name_translation.csv": "raw_category_translation",
}


def load_one(engine, file_name, table):
    started = time.time()
    df = pd.read_csv(RAW_DIR / file_name, dtype=str, encoding="utf-8-sig")
    df.to_sql(table, engine, if_exists="replace", index=False, chunksize=5000, method="multi")

    with engine.connect() as conn:
        db_rows = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()

    status = "OK" if db_rows == len(df) else "MISMATCH"
    print(f"[{status}] {file_name:<42} → {table:<26} csv={len(df):>9,} db={db_rows:>9,} ({time.time() - started:.1f}s)")
    return status == "OK"


def main():
    engine = get_engine()
    results = [load_one(engine, f, t) for f, t in SOURCES.items()]
    if not all(results):
        raise SystemExit("행 수가 맞지 않는 테이블이 있습니다. 위 MISMATCH 줄을 확인하세요.")
    print("raw 적재 완료")


if __name__ == "__main__":
    main()
