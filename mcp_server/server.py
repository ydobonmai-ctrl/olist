"""Olist 미니 DW를 Claude 같은 AI 에이전트가 쓰는 '검수 도구'로 노출하는 MCP 서버.

AI가 DB에 아무 SQL이나 날리게 두지 않고, 미리 정한 도구(tool)만 부르게 한다.
  - 품질 규칙 13개 실행 / 실행 이력 조회
  - 규칙을 어긴 '검수 대상 건' 샘플 조회
  - 공통 지표(KPI, 셀러 등급, 불만 유형) 조회
  - 읽기 전용 SQL (SELECT 한 문장, 행 수 제한, 금지어 차단)

MCP는 stdio 위의 JSON-RPC 2.0 이다. 이 PC의 Python이 3.9라 공식 SDK(3.10+) 대신
프로토콜의 필요한 부분(initialize, tools/list, tools/call)을 직접 구현했다.

등록 (Claude Code):
    claude mcp add olist-dw -- <프로젝트>\\.venv\\Scripts\\python.exe <프로젝트>\\mcp_server\\server.py
"""
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from sqlalchemy import text  # noqa: E402

import quality_checks  # noqa: E402  (규칙 13개를 파이프라인과 같은 정의로 재사용)
from config import get_engine  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "olist-dw", "version": "1.0.0"}
MAX_ROWS = 200

_engine = None


def engine():
    global _engine
    if _engine is None:
        _engine = get_engine()
    return _engine


def rows(sql, **params):
    with engine().connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


# ---------------------------------------------------------------- 도구 구현

# 규칙 위반 '건'을 직접 보여 주는 SQL. 위반 수만 아는 것보다, 어떤 건인지 봐야 원인을 고칠 수 있다.
VIOLATION_SAMPLES = {
    "배송 완료인데 도착일 없음":
        "SELECT order_id, order_status, purchased_at, delivered_customer_at FROM fact_orders "
        "WHERE order_status = 'delivered' AND delivered_customer_at IS NULL LIMIT :n",
    "도착일이 구매일보다 빠름":
        "SELECT order_id, purchased_at, delivered_customer_at FROM fact_orders "
        "WHERE delivered_customer_at < purchased_at LIMIT :n",
    "결제액과 상품가+배송비 차이 1 BRL 초과 (배송 완료)":
        "SELECT order_id, payment_value, items_price, items_freight, "
        "ROUND(payment_value - items_price - items_freight, 2) AS diff FROM fact_orders "
        "WHERE order_status = 'delivered' AND ABS(payment_value - items_price - items_freight) > 1 "
        "ORDER BY ABS(payment_value - items_price - items_freight) DESC LIMIT :n",
    "카테고리 미분류 상품":
        "SELECT product_id, category FROM dim_product WHERE category = 'unknown' LIMIT :n",
    "위치 좌표 없는 고객":
        "SELECT customer_id, state, city FROM dim_customer WHERE lat IS NULL LIMIT :n",
    "상품가·배송비 음수 없음":
        "SELECT order_id, product_id, price, freight_value FROM fact_order_items "
        "WHERE price < 0 OR freight_value < 0 LIMIT :n",
    "리뷰 점수 1~5 범위":
        "SELECT order_id, review_score FROM fact_orders "
        "WHERE review_score IS NOT NULL AND review_score NOT BETWEEN 1 AND 5 LIMIT :n",
}


def tool_run_quality_checks(_args):
    run_at = datetime.now().replace(microsecond=0)
    results = []
    with engine().begin() as conn:
        for name, severity, sql in quality_checks.CHECKS:
            v = int(conn.execute(text(sql)).scalar() or 0)
            results.append({"check": name, "severity": severity, "violations": v,
                            "status": "PASS" if v == 0 else ("FAIL" if severity == "error" else "WARN")})
            conn.execute(text("INSERT INTO dq_results VALUES (:r, :n, :s, :v, :p)"),
                         {"r": run_at, "n": name, "s": severity, "v": v, "p": v == 0})
    failed = [r["check"] for r in results if r["status"] == "FAIL"]
    return {"run_at": run_at, "passed": not failed, "failed_checks": failed, "results": results}


def tool_quality_history(args):
    n = min(int(args.get("runs", 5)), 30)
    return rows("""
        SELECT run_at,
               SUM(severity = 'error' AND passed = 0) AS errors,
               SUM(severity = 'warn'  AND passed = 0) AS warnings,
               SUM(violations)                       AS total_violations
        FROM dq_results GROUP BY run_at ORDER BY run_at DESC LIMIT :n""", n=n)


def tool_find_violations(args):
    check = args.get("check", "")
    if check not in VIOLATION_SAMPLES:
        return {"error": "샘플 조회를 지원하지 않는 규칙", "supported_checks": sorted(VIOLATION_SAMPLES)}
    n = min(int(args.get("limit", 20)), MAX_ROWS)
    return {"check": check, "rows": rows(VIOLATION_SAMPLES[check], n=n)}


def tool_get_kpi(_args):
    return rows("SELECT * FROM agg_kpi_overall")[0]


def tool_seller_health(args):
    grade = args.get("grade")
    n = min(int(args.get("limit", 20)), MAX_ROWS)
    if grade:
        if grade not in ("A", "B", "C", "D"):
            return {"error": "grade 는 A, B, C, D 중 하나"}
        return rows("SELECT * FROM agg_seller_health WHERE grade = :g ORDER BY health_score LIMIT :n",
                    g=grade, n=n)
    return rows("""SELECT grade, COUNT(*) AS sellers, AVG(health_score) AS avg_score
                   FROM agg_seller_health GROUP BY grade ORDER BY grade""")


def tool_complaint_breakdown(_args):
    return rows("""
        SELECT l.complaint_type,
               COUNT(*)                         AS reviews,
               COUNT(*) / SUM(COUNT(*)) OVER () AS share,
               AVG(COALESCE(o.is_late, 0))      AS actual_late_ratio
        FROM review_labels l JOIN fact_orders o ON o.order_id = l.order_id
        GROUP BY l.complaint_type ORDER BY reviews DESC""")


FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|truncate|replace|grant|revoke|"
                       r"call|load|into|outfile|dumpfile|sleep|benchmark|lock|set)\b", re.I)


def tool_run_readonly_sql(args):
    sql = (args.get("sql") or "").strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", sql, re.I):
        return {"error": "SELECT(또는 WITH) 한 문장만 허용"}
    if ";" in sql:
        return {"error": "여러 문장은 허용하지 않음"}
    if FORBIDDEN.search(sql):
        return {"error": "읽기 전용 도구: 변경·잠금·파일 관련 키워드는 차단"}
    with engine().connect() as conn:
        # 키워드 차단에 더해 DB 세션 자체도 읽기 전용으로 건다 (이중 안전장치)
        conn.execute(text("SET SESSION TRANSACTION READ ONLY"))
        conn.execute(text("SET SESSION MAX_EXECUTION_TIME = 10000"))  # 10초 넘는 조회 중단
        try:
            result = conn.execute(text(f"SELECT * FROM ({sql}) q LIMIT {MAX_ROWS}"))
            data = [dict(r._mapping) for r in result]
        finally:
            # 연결은 풀로 돌아가 다른 도구(품질 검사 기록 등)가 다시 쓰므로 원래대로 되돌린다
            conn.rollback()
            conn.execute(text("SET SESSION TRANSACTION READ WRITE"))
            conn.execute(text("SET SESSION MAX_EXECUTION_TIME = 0"))
            conn.commit()
    return {"row_limit": MAX_ROWS, "rows": data}


TOOLS = {
    "run_quality_checks": (tool_run_quality_checks,
        "품질 규칙 13개(키 무결성·값 범위·중복 등)를 지금 실행하고 결과를 dq_results 에 기록한다. "
        "error 규칙 위반이 있으면 passed=false.", {}),
    "quality_history": (tool_quality_history,
        "최근 품질 검사 실행 이력(실행별 error·warn 위반 수)을 본다.",
        {"runs": {"type": "integer", "description": "최근 몇 번의 실행 (기본 5, 최대 30)"}}),
    "find_violations": (tool_find_violations,
        "특정 품질 규칙을 어긴 실제 건을 샘플로 본다. 원인을 확인하거나 담당자에게 넘길 때 쓴다.",
        {"check": {"type": "string", "description": "규칙 이름 (run_quality_checks 결과의 check 값)"},
         "limit": {"type": "integer", "description": "최대 행 수 (기본 20)"}}),
    "get_kpi": (tool_get_kpi,
        "전체 KPI: 매출, 주문 수, 평균 리뷰, 지연율(주문 기준·품목 기준 두 정의).", {}),
    "seller_health": (tool_seller_health,
        "셀러 Health Score 등급. grade 를 주면 그 등급 셀러 목록, 없으면 등급별 요약.",
        {"grade": {"type": "string", "enum": ["A", "B", "C", "D"]},
         "limit": {"type": "integer"}}),
    "complaint_breakdown": (tool_complaint_breakdown,
        "불만 리뷰 유형별 비중과, 그 주문이 실제로 늦었는지 비율 (텍스트 분류 결과 × 주문 데이터).", {}),
    "run_readonly_sql": (tool_run_readonly_sql,
        "정해진 도구로 답할 수 없을 때만 쓰는 읽기 전용 SQL. SELECT/WITH 한 문장, 최대 200행, 10초 제한.",
        {"sql": {"type": "string", "description": "SELECT 문 (fact_orders, fact_order_items, dim_*, agg_* 테이블)"}}),
}


# ---------------------------------------------------------------- MCP 프로토콜

def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return str(o)


def handle(msg):
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": name, "description": desc,
                           "inputSchema": {"type": "object", "properties": props}}
                          for name, (_, desc, props) in TOOLS.items()]}
    if method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            raise ValueError(f"알 수 없는 도구: {name}")
        try:
            out = TOOLS[name][0](params.get("arguments") or {})
            return {"content": [{"type": "text",
                                 "text": json.dumps(out, ensure_ascii=False, default=_json_default)}],
                    "isError": isinstance(out, dict) and "error" in out}
        except Exception as e:  # DB 오류는 AI가 읽고 대응할 수 있게 도구 결과로 돌려준다
            return {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    raise LookupError(method)


def main():
    stdin = open(sys.stdin.fileno(), encoding="utf-8", closefd=False)
    out = open(sys.stdout.fileno(), "w", encoding="utf-8", closefd=False)
    for line in stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        if "id" not in msg:          # notification (예: notifications/initialized) 는 응답하지 않음
            continue
        try:
            resp = {"jsonrpc": "2.0", "id": msg["id"], "result": handle(msg)}
        except LookupError as e:
            resp = {"jsonrpc": "2.0", "id": msg["id"],
                    "error": {"code": -32601, "message": f"method not found: {e}"}}
        except Exception as e:
            resp = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": str(e)}}
        out.write(json.dumps(resp, ensure_ascii=False, default=_json_default) + "\n")
        out.flush()


if __name__ == "__main__":
    main()
