"""数据库连接与会话。开发默认 SQLite，生产通过 DATABASE_URL 切 MySQL。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

from .config import settings

Base = declarative_base()

connect_args = {}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, connect_args=connect_args, pool_pre_ping=True)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=True, autocommit=False)


def ensure_schema():
    """轻量迁移：给已存在的 SQLite 表补新列 + 确保外带占位桌台(id=0)存在（新库由 create_all 全量建表）。"""
    from sqlalchemy import inspect, text

    insp = inspect(engine)

    # SQLite 老库补新列（MySQL 生产库为全新 create_all，无需处理）
    if settings.database_url.startswith("sqlite"):
        migrations = {
            "dish": {"emoji": "VARCHAR(8) NOT NULL DEFAULT ''"},
            "order": {
                "discount": "NUMERIC(10, 2) NOT NULL DEFAULT 0",
                "paid_amount": "NUMERIC(10, 2)",
                "settle_note": "VARCHAR(255) NOT NULL DEFAULT ''",
                "is_takeout": "BOOLEAN NOT NULL DEFAULT 0",
                "utensil_fee": "NUMERIC(10, 2) NOT NULL DEFAULT 0",
            },
        }
        with engine.begin() as conn:
            for table, cols in migrations.items():
                if table not in insp.get_table_names():
                    continue
                existing = {c["name"] for c in insp.get_columns(table)}
                for col, ddl in cols.items():
                    if col not in existing:
                        # 表名/列名加双引号：order 等是 SQLite 保留字，不加引号会报语法错误
                        conn.execute(text('ALTER TABLE "%s" ADD COLUMN "%s" %s' % (table, col, ddl)))

    # 外带占位桌台（id=0）：外带单 table_id=0，开启外键后必须存在对应行。
    # 注意：MySQL 默认把显式 id=0 当作自增请求（会落到 17、18…），插入后需强制改回 0。
    if "dining_table" not in insp.get_table_names():
        return
    with engine.begin() as conn:
        if not conn.execute(text("SELECT COUNT(*) FROM dining_table WHERE name = '外带'")).scalar():
            conn.execute(text(
                "INSERT INTO dining_table (id, name, area, seats, qr_scene, enabled, dirty) "
                "VALUES (0, '外带', '外带', 0, '__takeout__', 0, 0)"
            ))
        conn.execute(text("UPDATE dining_table SET id = 0 WHERE name = '外带' AND id <> 0"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
