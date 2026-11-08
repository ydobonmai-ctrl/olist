"""MCP 서버를 실제 클라이언트처럼 stdio로 띄워 JSON-RPC 요청을 보내 보는 점검 스크립트.

    .venv\\Scripts\\python.exe mcp_server\\smoke_test.py
"""
import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent / "server.py"

proc = subprocess.Popen([sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        encoding="utf-8")
_id = 0


def call(method, params=None):
    global _id
    _id += 1
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": _id, "method": method, "params": params or {}}) + "\n")
    proc.stdin.flush()
    return json.loads(proc.stdout.readline())


def tool(name, **args):
    r = call("tools/call", {"name": name, "arguments": args})["result"]
    return r["isError"], json.loads(r["content"][0]["text"]) if not r["isError"] or r["content"][0]["text"].startswith("{") else r["content"][0]["text"]


init = call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}})
print("initialize:", init["result"]["serverInfo"])
proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")

tools = call("tools/list")["result"]["tools"]
print("tools:", [t["name"] for t in tools])

err, qc = tool("run_quality_checks")
print("run_quality_checks: passed=%s, failed=%s" % (qc["passed"], qc["failed_checks"]))
for r in qc["results"]:
    if r["status"] != "PASS":
        print("   ", r["status"], r["check"], r["violations"])

err, v = tool("find_violations", check="결제액과 상품가+배송비 차이 1 BRL 초과 (배송 완료)", limit=3)
print("find_violations:", v["rows"])

err, k = tool("get_kpi")
print("get_kpi:", {key: k[key] for key in list(k)[:6]})

err, s = tool("seller_health")
print("seller_health:", s)

err, sql = tool("run_readonly_sql", sql="SELECT c.state, COUNT(*) AS orders, AVG(o.is_late) AS late_ratio "
                                       "FROM fact_orders o JOIN dim_customer c ON c.customer_id = o.customer_id "
                                       "WHERE o.order_status = 'delivered' GROUP BY c.state "
                                       "ORDER BY late_ratio DESC LIMIT 3")
print("run_readonly_sql:", sql if err else sql["rows"])

err, after = tool("run_quality_checks")   # 읽기 전용 조회 뒤에도 기록(쓰기)이 정상인지
print("write after readonly:", "ok" if not err else after)

err, h = tool("quality_history", runs=2)
print("quality_history:", h)

err, blocked = tool("run_readonly_sql", sql="DELETE FROM fact_orders")
print("blocked write:", err, blocked)
err, blocked = tool("run_readonly_sql", sql="SELECT 1; DROP TABLE fact_orders")
print("blocked multi:", err, blocked)

print("unknown method:", call("foo/bar").get("error"))
proc.stdin.close()
proc.wait(timeout=10)
