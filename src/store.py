"""SQLite 状态库：报告索引与推送幂等。"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            date        TEXT PRIMARY KEY,
            path        TEXT NOT NULL,
            item_count  INTEGER NOT NULL,
            overview    TEXT,
            created_at  TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pushed (
            date        TEXT NOT NULL,
            url_hash    TEXT NOT NULL,
            pushed_at   TEXT NOT NULL,
            PRIMARY KEY (date, url_hash)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repo_seen (
            url_hash    TEXT PRIMARY KEY,
            full_name   TEXT,
            last_date   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pushed_days (
            date        TEXT PRIMARY KEY,
            pushed_at   TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def save_report(conn: sqlite3.Connection, date: str, path: str, count: int, overview: str) -> None:
    conn.execute(
        "INSERT INTO reports(date, path, item_count, overview, created_at) VALUES(?,?,?,?,?) "
        "ON CONFLICT(date) DO UPDATE SET path=excluded.path, item_count=excluded.item_count, "
        "overview=excluded.overview, created_at=excluded.created_at",
        (date, path, count, overview, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def report_exists(conn: sqlite3.Connection, date: str) -> bool:
    row = conn.execute("SELECT 1 FROM reports WHERE date=?", (date,)).fetchone()
    return row is not None


def list_reports(conn: sqlite3.Connection, limit: int = 90) -> list[dict]:
    rows = conn.execute(
        "SELECT date, path, item_count, overview FROM reports ORDER BY date DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_pushed(conn: sqlite3.Connection, date: str, url_hash: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO pushed(date, url_hash, pushed_at) VALUES(?,?,?)",
        (date, url_hash, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def pushed_count(conn: sqlite3.Connection, date: str) -> int:
    row = conn.execute("SELECT COUNT(*) AS c FROM pushed WHERE date=?", (date,)).fetchone()
    return int(row["c"]) if row else 0


def recent_repo_hashes(conn: sqlite3.Connection, days: int = 14) -> set[str]:
    """近 N 天已经上过榜的仓库，避免天天重复展示同一批项目。"""
    rows = conn.execute(
        "SELECT url_hash FROM repo_seen WHERE date(last_date) >= date('now', ?)",
        (f"-{days} day",),
    ).fetchall()
    return {row["url_hash"] for row in rows}


def mark_repos(conn: sqlite3.Connection, repos: list[dict], date: str) -> None:
    conn.executemany(
        "INSERT INTO repo_seen(url_hash, full_name, last_date) VALUES(?,?,?) "
        "ON CONFLICT(url_hash) DO UPDATE SET last_date=excluded.last_date",
        [(r["url_hash"], r["name"], date) for r in repos],
    )
    conn.commit()


def day_pushed(conn: sqlite3.Connection, date: str) -> bool:
    return conn.execute("SELECT 1 FROM pushed_days WHERE date=?", (date,)).fetchone() is not None


def mark_day_pushed(conn: sqlite3.Connection, date: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO pushed_days(date, pushed_at) VALUES(?,?)",
        (date, datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
