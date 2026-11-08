"""전체 파이프라인 실행: raw 적재 → SQL(staging·mart·지표 뷰) → 품질 검사 → pandas 결과 대조 → RFM 고객 세그먼트 → 리뷰 불만 유형 분류.

    python pipeline/run_pipeline.py            # 처음부터 끝까지
    python pipeline/run_pipeline.py --skip-raw # raw 적재는 건너뛰고 SQL만 다시 실행
"""
import argparse
import time

from sqlalchemy import text

import customer_segments
import load_raw
import review_classifier
import quality_checks
import validate
from config import SQL_DIR, get_engine


def split_statements(sql):
    """세미콜론 기준으로 문장을 나눈다. -- 주석 줄은 버린다."""
    lines = [l for l in sql.splitlines() if not l.strip().startswith("--")]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def run_sql_files(engine):
    files = sorted(SQL_DIR.glob("[0-9][0-9]_*.sql"))
    for path in files:
        statements = split_statements(path.read_text(encoding="utf-8"))
        if not statements:
            print(f"[SKIP] {path.name} (아직 작성된 SQL 없음)")
            continue
        started = time.time()
        with engine.begin() as conn:
            for stmt in statements:
                try:
                    conn.execute(text(stmt))
                except Exception as e:
                    first_line = stmt.splitlines()[0][:80]
                    raise SystemExit(f"[FAIL] {path.name}: {first_line}\n       {getattr(e, 'orig', e)}")
        print(f"[OK]   {path.name} ({len(statements)}개 문장, {time.time() - started:.1f}s)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-raw", action="store_true")
    args = parser.parse_args()

    if not args.skip_raw:
        load_raw.main()
    run_sql_files(get_engine())
    quality_checks.main()
    validate.main()
    customer_segments.main()
    review_classifier.main()
    print("파이프라인 완료 (검증 보고서: validation_report.md)")


if __name__ == "__main__":
    main()
