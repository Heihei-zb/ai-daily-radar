"""渲染：单文件 HTML，CSS/JS 全内联，无外部依赖。"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .rank import RankResult
from .store import list_reports
from .util import ROOT, circled, fmt_date_cn, fmt_weekday_cn

CAT_STYLE = {
    "模型发布": ("chip-model", "#185FA5"),
    "产品应用": ("chip-product", "#0F6E56"),
    "开源生态": ("chip-oss", "#993C1D"),
    "融资并购": ("chip-fund", "#72243E"),
    "研究论文": ("chip-paper", "#534AB7"),
    "行业动态": ("chip-news", "#5F5E5A"),
}


def _env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(ROOT / "templates")),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


FOCUS_STYLE = {
    "汽车数据": "chip-model",
    "新片剪辑": "chip-fund",
    "数据分析": "chip-product",
    "高热 AI": "chip-paper",
    "通用 AI": "chip-news",
}


def _decorate(item: dict, index: int, max_stars: int = 0) -> dict:
    chip, color = CAT_STYLE.get(item["category"], CAT_STYLE["行业动态"])
    item = dict(item)
    item["no"] = circled(index)
    item["cls"] = chip
    item["color"] = color
    focus = item.get("extra", {}).get("focus", "")
    item["focus_cls"] = FOCUS_STYLE.get(focus, "chip-news")
    if max_stars and item["extra"].get("stars"):
        item["pct"] = round(item["extra"]["stars"] / max_stars * 100, 1)
    else:
        item["pct"] = 0
    return item


def render_daily(
    result: RankResult,
    target_date: datetime,
    docs_dir: Path,
    db_conn,
    generated_at: str,
    logger: logging.Logger,
    prev_href: str = "",
) -> Path:
    docs_dir.mkdir(parents=True, exist_ok=True)
    env = _env()

    items = [_decorate(it, i + 1) for i, it in enumerate(result.items)]
    max_stars = max((r["extra"].get("stars", 0) for r in result.repos), default=0) or 1
    repos = [_decorate(r, 0) for r in result.repos]
    for repo in repos:
        repo["pct"] = max(6.0, round(repo["extra"].get("stars", 0) / max_stars * 100, 1))
    papers = [_decorate(p, i + 1) for i, p in enumerate(result.papers)]

    from collections import Counter

    counter = Counter(it["category"] for it in items)
    top = max(counter.values()) if counter else 1
    cat_stats = [
        {
            "name": name,
            "count": count,
            "pct": round(count / top * 100, 1),
            "color": CAT_STYLE.get(name, CAT_STYLE["行业动态"])[1],
        }
        for name, count in counter.most_common()
    ]

    # 只统计真正贡献了入选条目的信源，避免「10 个信源」但实际只有 4 家在出稿
    src_count = len(
        {it["source"] for it in items}
        | {r["source"] for r in repos}
        | {p["source"] for p in papers}
    )

    kpi = {
        "count": len(items),
        "sources": src_count,
        "avg": round(sum(it["score"] for it in items) / len(items), 1) if items else 0,
        "repos": len(repos),
        "cats": len(cat_stats),
    }

    hero = items[0] if items else None
    cards = items[1:]

    html = env.get_template("daily.html.j2").render(
        date_cn=fmt_date_cn(target_date),
        weekday_cn=fmt_weekday_cn(target_date),
        date_iso=target_date.strftime("%Y-%m-%d"),
        overview=result.overview,
        generated_at=generated_at,
        kpi=kpi,
        cat_stats=cat_stats,
        hero=hero,
        cards=cards,
        repos=repos,
        papers=papers,
        used_llm=result.used_llm,
        prev_href=prev_href,
    )

    out = docs_dir / f"{target_date:%Y-%m-%d}.html"
    out.write_text(html, encoding="utf-8")
    shutil.copyfile(out, docs_dir / "index.html")
    logger.info("渲染完成：%s（%.0f KB）", out.name, len(html.encode("utf-8")) / 1024)
    return out


def render_archive(docs_dir: Path, conn, logger: logging.Logger) -> Path:
    docs_dir.mkdir(parents=True, exist_ok=True)
    env = _env()
    rows = list_reports(conn, limit=120)
    for row in rows:
        dt = datetime.strptime(row["date"], "%Y-%m-%d")
        row["label"] = f"{dt.month} 月 {dt.day} 日"
        row["weekday"] = fmt_weekday_cn(dt)
        row["href"] = f"./{row['date']}.html"
    html = env.get_template("archive.html.j2").render(reports=rows)
    out = docs_dir / "archive.html"
    out.write_text(html, encoding="utf-8")
    logger.info("归档页更新：%d 期", len(rows))
    return out
