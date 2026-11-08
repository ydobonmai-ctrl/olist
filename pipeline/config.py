"""DB 접속 정보와 경로 설정. 비밀번호는 코드에 쓰지 않고 .env 파일에서 읽는다."""
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

RAW_DIR = Path(os.environ["OLIST_RAW_DIR"])
SQL_DIR = ROOT / "sql"
DB_NAME = os.getenv("MYSQL_DATABASE", "olist_dw")


def _url(database=None):
    return URL.create(
        "mysql+pymysql",
        username=os.getenv("MYSQL_USER", "root"),
        password=os.environ["MYSQL_PASSWORD"],
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        database=database,
        query={"charset": "utf8mb4"},
    )


def get_engine():
    """olist_dw 데이터베이스가 없으면 만든 뒤 그 DB에 연결한 엔진을 돌려준다."""
    server = create_engine(_url())
    with server.connect() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS {DB_NAME} CHARACTER SET utf8mb4"))
    server.dispose()
    return create_engine(_url(DB_NAME), pool_pre_ping=True)
