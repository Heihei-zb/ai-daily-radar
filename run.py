"""AI 行业日报 · 每日生成 + 企微推送。

用法：
    python run.py                     # 生成今天，发布并推送
    python run.py --dry-run           # 只生成网页，不发布不推送
    python run.py --dry-run --no-llm  # 无 key 也能跑通全链路（规则兜底）
    python run.py --date 2026-11-10   # 补跑指定日期
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import yaml  # noqa: E402

from src import clean as clean_mod  # noqa: E402
from src import rank as rank_mod  # noqa: E402
from src import render as render_mod  # noqa: E402
from src import store  # noqa: E402
from src.deploy import deploy_site  # noqa: E402
from src.fetch import fetch_all  # noqa: E402
from src.push_wecom import push, push_cli  # noqa: E402
from src.util import TZ, fmt_date_cn, load_env, now_tz, setup_logger  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI 行业日报生成器")
    parser.add_argument("--date", help="生成指定日期（YYYY-MM-DD），默认今天")
    parser.add_argument("--dry-run", action="store_true", help="只渲染网页，不发布不推送")
    parser.add_argument("--no-llm", action="store_true", help="强制走规则兜底，不调用大模型")
    parser.add_argument("--no-push", action="store_true", help="跳过企微推送")
    parser.add_argument("--no-deploy", action="store_true", help="跳过 git 发布")
    parser.add_argument("--push-only", action="store_true", help="只重发已生成的那一期，不重新抓取")
    parser.add_argument("--force-push", action="store_true", help="当天已推送过也再发一次")
    parser.add_argument("--limit", type=int, default=None, help="覆盖 settings 中的 max_items")
    return parser.parse_args()


def load_config() -> tuple[dict, dict]:
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    return settings, sources


def compute_window(target: datetime | None, window_hours: int, now: datetime):
    if target is None:
        return now - timedelta(hours=window_hours), now, now
    start = target.replace(hour=0, minute=0, second=0, microsecond=0)
    end = min(start + timedelta(days=1), now + timedelta(hours=2))
    return start, end, target


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    args = parse_args()
    load_env(ROOT / ".env")
    cfg, src_cfg = load_config()
    if args.limit:
        cfg["app"]["max_items"] = args.limit

    logger = setup_logger(ROOT / "logs")

    target = None
    if args.date:
        try:
            target = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=TZ)
        except ValueError:
            logger.error("日期格式不对，应为 YYYY-MM-DD")
            return 2

    now = now_tz()
    window_start, window_end, report_dt = compute_window(
        target, int(cfg["app"].get("window_hours", 26)), now
    )
    date_iso = report_dt.strftime("%Y-%m-%d")
    date_cn = fmt_date_cn(report_dt)

    base = str(cfg.get("site", {}).get("base_url", "")).rstrip("/")
    page_url = f"{base}/{date_iso}.html" if base else ""
    snapshot_path = ROOT / cfg["app"].get("data_dir", "data") / "reports" / f"{date_iso}.json"

    # 只重发：读取已生成的那一期，不重新抓取，也不占用仓库去重名额
    if args.push_only:
        if not snapshot_path.exists():
            logger.error("找不到 %s 的生成记录，无法重发", date_iso)
            return 1
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        logger.info("重发 %s：%d 条", date_iso, len(snapshot.get("items", [])))
        channel = str(cfg.get("push", {}).get("channel", "cli"))
        sent = False
        if channel in ("cli", "both"):
            sent = push_cli(snapshot["items"], snapshot.get("date_cn", date_cn), snapshot.get("overview", ""), page_url, cfg, logger, dry_run=args.dry_run) or sent
        if channel in ("webhook", "both"):
            sent = push(snapshot["items"], snapshot.get("date_cn", date_cn), snapshot.get("overview", ""), page_url, cfg, logger, dry_run=args.dry_run) or sent
        # 补发同样要落「已推送」标记，否则定时任务会再发一条
        if sent and not args.dry_run:
            conn = store.connect(ROOT / cfg["app"].get("data_dir", "data") / "state.db")
            store.mark_day_pushed(conn, date_iso)
            logger.info("已标记 %s 为已推送", date_iso)
        return 0

    logger.info("=== 生成 %s，时间窗 %s ~ %s ===", date_iso, window_start.strftime("%m-%d %H:%M"), window_end.strftime("%m-%d %H:%M"))

    # 1. 抓取（先把已上榜的 GitHub 项目告诉抓取层，让它顺延选下一个）
    conn = store.connect(ROOT / cfg["app"].get("data_dir", "data") / "state.db")
    seen_repos = store.recent_repo_hashes(conn, days=10)
    for source in src_cfg["sources"]:
        if source.get("type") == "github":
            source["_exclude"] = seen_repos

    articles, report = fetch_all(src_cfg["sources"])
    ok_sources = sum(1 for r in report if r["ok"] and r["count"] > 0)
    logger.info("抓取完成：%d 条，%d 个信源有产出", len(articles), ok_sources)
    for row in report:
        if not row["ok"]:
            logger.warning("  ✗ %s：%s", row["name"], row.get("error", ""))

    if not articles:
        logger.error("所有信源均无产出，终止")
        return 1

    # 兜底：抓取层没处理掉的历史项目再过滤一次
    before = len(articles)
    articles = [
        a for a in articles if not (a.source_id == "github" and a.url_hash in seen_repos)
    ]
    if before != len(articles):
        logger.info("GitHub 榜剔除近 10 天已上榜项目 %d 个", before - len(articles))

    # 原始快照，便于事后核对与复现
    raw_dir = ROOT / cfg["app"].get("data_dir", "data") / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{date_iso}.json").write_text(
        json.dumps([a.as_dict() for a in articles], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )

    # 2. 清洗
    cleaned = clean_mod.clean(
        articles,
        src_cfg["sources"],
        src_cfg.get("ai_keywords", []),
        window_start,
        window_end,
        language=str(cfg["app"].get("language", "zh")),
    )

    # 3. 打分与成稿
    result = rank_mod.rank(cleaned, cfg, date_cn, logger, force_no_llm=args.no_llm)
    logger.info(
        "入选：新闻 %d 条 / 开源 %d 个 / 论文 %d 篇（%s）",
        len(result.items), len(result.repos), len(result.papers),
        "LLM" if result.used_llm else "规则兜底",
    )
    if result.note:
        logger.info("备注：%s", result.note)

    # 4. 渲染
    docs_dir = ROOT / cfg["app"].get("docs_dir", "docs")
    prev_href = ""
    existing = sorted(p.stem for p in docs_dir.glob("*.html") if p.stem[:4].isdigit())
    older = [d for d in existing if d < date_iso]
    if older:
        prev_href = f"./{older[-1]}.html"

    out = render_mod.render_daily(
        result,
        report_dt,
        docs_dir,
        None,
        now.strftime("%m-%d %H:%M"),
        logger,
        prev_href=prev_href,
    )

    store.save_report(conn, date_iso, out.name, len(result.items), result.overview)
    if result.repos:
        store.mark_repos(
            conn,
            [
                {
                    "url_hash": hashlib.sha1(r["url"].encode("utf-8")).hexdigest(),
                    "name": r["title"],
                }
                for r in result.repos
            ],
            date_iso,
        )
    render_mod.render_archive(docs_dir, conn, logger)

    # 保存当期快照，供 --push-only 重发
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_path.write_text(
        json.dumps(
            {"date": date_iso, "date_cn": date_cn, "overview": result.overview, "items": result.items},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    # 5. 发布 + 推送
    if not args.no_deploy:
        deploy_site(cfg, f"chore: 更新 {date_iso} AI 日报", logger, dry_run=args.dry_run)

    if not args.no_push:
        if store.day_pushed(conn, date_iso) and not args.force_push:
            logger.info("今日已推送过，跳过（要重发加 --force-push）")
        else:
            channel = str(cfg.get("push", {}).get("channel", "cli"))
            sent = False
            if channel in ("cli", "both"):
                sent = push_cli(
                    result.items,
                    date_cn,
                    result.overview,
                    page_url,
                    cfg,
                    logger,
                    dry_run=args.dry_run,
                ) or sent
            if channel in ("webhook", "both"):
                sent = push(
                    result.items,
                    date_cn,
                    result.overview,
                    page_url,
                    cfg,
                    logger,
                    dry_run=args.dry_run,
                ) or sent
            if sent and not args.dry_run:
                store.mark_day_pushed(conn, date_iso)

    logger.info("完成：%s", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
