"""리뷰 텍스트(비정형 데이터) → LLM 분류·임베딩 → MySQL 적재.

    python pipeline/review_ai.py classify   # 불만 리뷰(1~3점) 전체를 불만 유형으로 분류 → review_labels
    python pipeline/review_ai.py embed      # 리뷰 본문 전체를 벡터로 변환 → review_embeddings
    python pipeline/review_ai.py status     # 진행 상황

- OpenAI API 사용 (gpt-4o-mini 분류, text-embedding-3-small 임베딩). 키는 .env 의 OPENAI_API_KEY.
- 이미 처리한 리뷰는 건너뛰므로 중간에 끊겨도 다시 실행하면 이어서 처리한다.
- 요청 한도(429)·서버 오류에 걸리면 기다렸다가 재시도한다.
"""
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx
import numpy as np
from sqlalchemy import text

from config import get_engine

API = "https://api.openai.com/v1"
CLASSIFY_MODEL = "gpt-4o-mini"
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 256           # 원래 1536차원 → 256차원으로 줄여 저장·검색 비용 절감
CLASSIFY_BATCH = 40       # 요청 1번에 리뷰 40건
EMBED_BATCH = 500         # 임베딩 요청 1번에 500건
WORKERS = 4               # 동시 요청 수

COMPLAINT_TYPES = {
    "delivery_delay": "배송 지연 (늦게 도착, 예정일 초과)",
    "not_received": "미배송·분실 (아직 못 받음)",
    "damaged_defective": "파손·불량 (깨짐, 작동 안 함)",
    "wrong_or_missing_item": "오배송·구성 누락 (다른 상품, 일부만 도착)",
    "quality_mismatch": "품질·설명 불일치 (기대보다 나쁨, 사진과 다름)",
    "seller_service": "판매자·고객 응대 (연락 안 됨, 환불·교환 문제)",
    "positive_or_neutral": "불만 아님 (점수는 낮지만 내용은 긍정·중립)",
    "other": "기타",
}


def headers():
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        raise SystemExit(".env 에 OPENAI_API_KEY 를 넣어 주세요.")
    return {"Authorization": f"Bearer {key}"}


def post(client, path, body, tries=6):
    for attempt in range(tries):
        r = client.post(f"{API}{path}", json=body, headers=headers(), timeout=120)
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503):
            wait = 10 * (attempt + 1)
            try:
                reason = r.json()["error"]["message"][:160]
            except Exception:
                reason = r.text[:160]
            print(f"  {r.status_code} 응답 → {wait}초 대기 후 재시도 ({reason})")
            time.sleep(wait)
            continue
        raise SystemExit(f"API 오류 {r.status_code}: {r.text[:300]}")
    raise SystemExit("재시도 한도 초과. 잠시 뒤 다시 실행하면 이어서 처리합니다.")


def ensure_tables(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS review_labels (
            order_id        VARCHAR(32)  NOT NULL PRIMARY KEY,
            review_score    TINYINT      NOT NULL,
            complaint_type  VARCHAR(30)  NOT NULL,
            model           VARCHAR(40)  NOT NULL,
            labeled_at      DATETIME     NOT NULL,
            INDEX ix_review_labels_type (complaint_type)
        )"""))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS review_embeddings (
            order_id    VARCHAR(32)  NOT NULL PRIMARY KEY,
            embedding   BLOB         NOT NULL,      -- float32 × 256
            model       VARCHAR(40)  NOT NULL,
            embedded_at DATETIME     NOT NULL
        )"""))
    # 다른 모델로 만든 결과가 섞이지 않게 정리 (같은 공간의 벡터끼리만 비교 가능)
    conn.execute(text("DELETE FROM review_labels WHERE model <> :m"), {"m": CLASSIFY_MODEL})
    conn.execute(text("DELETE FROM review_embeddings WHERE model <> :m"), {"m": EMBED_MODEL})


def run_parallel(fn, batches):
    with ThreadPoolExecutor(WORKERS) as pool:
        yield from pool.map(fn, batches)


# ---------------------------------------------------------------------------
# 1) 불만 유형 분류
# ---------------------------------------------------------------------------
PROMPT = """You label Brazilian e-commerce customer reviews (Portuguese) by complaint type.
Allowed labels:
{labels}
Pick the single main complaint for each review. Use positive_or_neutral if the text has no complaint.
Return JSON: {{"items": [{{"id": <id>, "label": <label>}}, ...]}} with one item per review.

Reviews:
{reviews}"""

SCHEMA = {"name": "labels", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["items"],
    "properties": {"items": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "label"],
        "properties": {"id": {"type": "integer"}, "label": {"type": "string", "enum": list(COMPLAINT_TYPES)}}}}}}}


def classify():
    engine = get_engine()
    with engine.begin() as conn:
        ensure_tables(conn)
        todo = conn.execute(text("""
            SELECT r.order_id, r.review_score, CONCAT_WS(' / ', r.review_comment_title, r.review_comment_message) AS body
            FROM stg_order_reviews r
            LEFT JOIN review_labels l ON l.order_id = r.order_id
            WHERE r.review_score <= 3 AND r.review_comment_message IS NOT NULL AND l.order_id IS NULL
            ORDER BY r.order_id""")).fetchall()
    print(f"분류할 리뷰 {len(todo):,}건 (모델 {CLASSIFY_MODEL}, {CLASSIFY_BATCH}건씩 {WORKERS}개 동시)")
    labels_txt = "\n".join(f"- {k}: {v}" for k, v in COMPLAINT_TYPES.items())
    batches = [todo[i:i + CLASSIFY_BATCH] for i in range(0, len(todo), CLASSIFY_BATCH)]
    usage = {"in": 0, "out": 0}

    def work(batch):
        reviews = "\n".join(f"[{i}] ({row.review_score} stars) {row.body[:600]}" for i, row in enumerate(batch))
        with httpx.Client() as client:
            data = post(client, "/chat/completions", {
                "model": CLASSIFY_MODEL, "temperature": 0,
                "response_format": {"type": "json_schema", "json_schema": SCHEMA},
                "messages": [{"role": "user", "content": PROMPT.format(labels=labels_txt, reviews=reviews)}],
            })
        usage["in"] += data["usage"]["prompt_tokens"]
        usage["out"] += data["usage"]["completion_tokens"]
        items = json.loads(data["choices"][0]["message"]["content"])["items"]
        by_id = {a["id"]: a["label"] for a in items}
        now = datetime.now().replace(microsecond=0)
        return [{"o": row.order_id, "s": row.review_score, "t": by_id[i], "m": CLASSIFY_MODEL, "at": now}
                for i, row in enumerate(batch) if i in by_id]

    saved = 0
    for n, values in enumerate(run_parallel(work, batches), 1):
        if values:
            with engine.begin() as conn:
                conn.execute(text("INSERT IGNORE INTO review_labels VALUES (:o, :s, :t, :m, :at)"), values)
        saved += len(values)
        if n % 25 == 0 or n == len(batches):
            print(f"  {saved:,}/{len(todo):,}")
    cost = usage["in"] / 1e6 * 0.15 + usage["out"] / 1e6 * 0.60
    print(f"분류 완료 — 토큰 입력 {usage['in']:,} / 출력 {usage['out']:,}, 예상 비용 약 ${cost:.3f}")


# ---------------------------------------------------------------------------
# 2) 임베딩 (의미 검색용)
# ---------------------------------------------------------------------------
def embed_texts(client, texts, task_type=None):
    """정규화된 벡터를 돌려준다 (내적 = 코사인 유사도). task_type 은 이전 Gemini 버전과의 호환용."""
    data = post(client, "/embeddings", {"model": EMBED_MODEL, "input": texts, "dimensions": EMBED_DIM})
    vecs = np.array([e["embedding"] for e in sorted(data["data"], key=lambda e: e["index"])], dtype=np.float32)
    return vecs / np.linalg.norm(vecs, axis=1, keepdims=True)


def embed():
    engine = get_engine()
    with engine.begin() as conn:
        ensure_tables(conn)
        todo = conn.execute(text("""
            SELECT r.order_id, CONCAT_WS(' / ', r.review_comment_title, r.review_comment_message) AS body
            FROM stg_order_reviews r
            LEFT JOIN review_embeddings e ON e.order_id = r.order_id
            WHERE r.review_comment_message IS NOT NULL AND e.order_id IS NULL
            ORDER BY r.order_id""")).fetchall()
    print(f"임베딩할 리뷰 {len(todo):,}건 (모델 {EMBED_MODEL}, {EMBED_DIM}차원)")
    batches = [todo[i:i + EMBED_BATCH] for i in range(0, len(todo), EMBED_BATCH)]

    def work(batch):
        with httpx.Client() as client:
            vecs = embed_texts(client, [row.body[:1000] for row in batch])
        now = datetime.now().replace(microsecond=0)
        return [{"o": row.order_id, "e": vec.tobytes(), "m": EMBED_MODEL, "at": now} for row, vec in zip(batch, vecs)]

    saved = 0
    for values in run_parallel(work, batches):
        with engine.begin() as conn:
            conn.execute(text("INSERT IGNORE INTO review_embeddings VALUES (:o, :e, :m, :at)"), values)
        saved += len(values)
        print(f"  {saved:,}/{len(todo):,}")
    print("임베딩 완료")


def status():
    with get_engine().begin() as conn:
        ensure_tables(conn)
        target = conn.execute(text("SELECT COUNT(*) FROM stg_order_reviews WHERE review_score <= 3 AND review_comment_message IS NOT NULL")).scalar()
        labeled = conn.execute(text("SELECT COUNT(*) FROM review_labels")).scalar()
        total = conn.execute(text("SELECT COUNT(*) FROM stg_order_reviews WHERE review_comment_message IS NOT NULL")).scalar()
        embedded = conn.execute(text("SELECT COUNT(*) FROM review_embeddings")).scalar()
    print(f"분류: {labeled:,}/{target:,}  임베딩: {embedded:,}/{total:,}")


if __name__ == "__main__":
    {"classify": classify, "embed": embed, "status": status}[sys.argv[1] if len(sys.argv) > 1 else "status"]()
