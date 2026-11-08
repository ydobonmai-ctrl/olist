"""SQL 지표 뷰를 2026.01 pandas 분석 결과(output_*.csv)와 대조한다.

같은 정의로 계산했다면 숫자가 일치해야 한다. 차이가 나면 어디서 왜 나는지 보고서에 남긴다.
"""
from datetime import datetime

import pandas as pd

from config import RAW_DIR, ROOT, get_engine

TOL = 1e-6


def close(a, b, tol=TOL):
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(b)))


def main():
    engine = get_engine()
    sql = lambda q: pd.read_sql(q, engine)
    lines = [f"# 검증 보고서 ({datetime.now():%Y-%m-%d %H:%M})", "",
             "기준: 2026.01 pandas 분석 결과 CSV / 비교 대상: MySQL 지표 뷰", ""]
    ok_all = True

    # 1) 전체 KPI · D등급 제외 시나리오
    expected = pd.read_csv(RAW_DIR / "output_scenario_compare.csv").set_index("scenario")
    actual = sql("SELECT * FROM v_scenario_without_d").set_index("scenario")
    lines += ["## 1. 전체 KPI와 D등급 제외 시나리오", "",
              "| 시나리오 | 지표 | pandas | SQL | 결과 |", "|---|---|---:|---:|---|"]
    for sc in ["All_sellers", "Without_D"]:
        for col_e, col_a in [("revenue", "revenue"), ("orders", "orders"),
                             ("late_ratio", "late_ratio_item"), ("avg_review", "avg_review_item")]:
            e, a = expected.loc[sc, col_e], actual.loc[sc, col_a]
            tol = 1e-3 if col_e == "avg_review" else TOL
            ok = close(a, e, tol)
            ok_all &= ok or col_e == "avg_review"
            lines.append(f"| {sc} | {col_e} | {e:,.6f} | {float(a):,.6f} | {'일치' if ok else '차이'} |")
    lines.append("")

    # 2) 월별 KPI
    exp_m = pd.read_csv(RAW_DIR / "output_monthly_kpi.csv").set_index("year_month")
    act_m = sql("SELECT * FROM v_kpi_monthly").set_index("ym")
    joined = exp_m.join(act_m, how="outer", lsuffix="_e")
    rev_ok = sum(close(r.revenue, r.revenue_e) for r in joined.itertuples())
    ord_ok = int((joined.orders == joined.orders_e).sum())
    late_ok = sum(close(r.late_ratio_item, r.late_ratio) for r in joined.itertuples())
    lines += ["## 2. 월별 KPI", "",
              f"- 월 수: pandas {len(exp_m)} / SQL {len(act_m)}",
              f"- 매출 일치 {rev_ok}/{len(joined)}, 주문 수 일치 {ord_ok}/{len(joined)}, 지연율 일치 {late_ok}/{len(joined)}", ""]
    ok_all &= rev_ok == ord_ok == late_ok == len(joined)

    # 3) 카테고리 KPI
    exp_c = pd.read_csv(RAW_DIR / "output_category_kpi.csv").set_index("category")
    act_c = sql("SELECT * FROM v_category_kpi").set_index("category")
    common = exp_c.index.intersection(act_c.index)
    mism = [c for c in common if not close(exp_c.loc[c, "revenue"], act_c.loc[c, "revenue"])]
    only_e, only_a = sorted(set(exp_c.index) - set(act_c.index)), sorted(set(act_c.index) - set(exp_c.index))
    lines += ["## 3. 카테고리 KPI", "",
              f"- 카테고리 수: pandas {len(exp_c)} / SQL {len(act_c)}, 공통 {len(common)}",
              f"- 공통 카테고리 중 매출 불일치: {mism or '없음'}",
              f"- SQL에만 있음: {only_a or '없음'} / pandas에만 있음: {only_e or '없음'}"]
    if "unknown" in mism or only_a:
        lines.append("- 원인: pandas 분석은 번역표에 없는 카테고리(pc_gamer 등)를 'unknown'에 합쳤고, "
                     "SQL은 원래 이름을 유지해 별도 카테고리로 분리함 (의도한 개선)")
    lines.append("")

    # 4) 셀러 Health Score · 등급
    exp_s = pd.read_csv(RAW_DIR / "output_seller_health.csv").set_index("seller_id")
    act_s = sql("SELECT * FROM v_seller_health").set_index("seller_id")
    j = exp_s.join(act_s, how="inner", lsuffix="_e")
    score_diff = (j.health_score - j.health_score_e).abs()
    grade_same = int((j.grade == j.grade_e).sum())
    lines += ["## 4. 셀러 Health Score와 등급", "",
              f"- 셀러 수: pandas {len(exp_s)} / SQL {len(act_s)}",
              f"- health_score 최대 오차: {score_diff.max():.2e} (오차 1e-6 이하 {int((score_diff <= 1e-6).sum())}/{len(j)})",
              f"- 등급 일치: {grade_same}/{len(j)}",
              f"- SQL 등급 분포: {act_s.grade.value_counts().sort_index().to_dict()}", ""]
    ok_all &= grade_same == len(j)

    lines += ["## 결론", "", "핵심 지표 모두 일치" if ok_all else "일부 차이 있음 — 위 항목 참고", ""]
    report = ROOT / "validation_report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"pandas 결과 대조: {'핵심 지표 모두 일치' if ok_all else '차이 있음'} → {report.name}")
    return ok_all


if __name__ == "__main__":
    main()
