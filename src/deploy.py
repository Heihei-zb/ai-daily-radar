"""发布：把 docs/ 推到远端，由 GitHub Pages 自动更新。"""
from __future__ import annotations

import logging
import subprocess
import time

from .util import ROOT


def _run(args: list[str], logger: logging.Logger) -> bool:
    result = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        logger.warning("git %s 失败：%s", " ".join(args[:3]), (result.stderr or result.stdout).strip()[:200])
        return False
    return True


def deploy_site(cfg: dict, message: str, logger: logging.Logger, dry_run: bool = False) -> bool:
    deploy_cfg = cfg.get("deploy", {})
    if not deploy_cfg.get("enabled", True):
        logger.info("自动发布已禁用")
        return False

    if not (ROOT / ".git").exists():
        logger.warning("项目尚未初始化为 git 仓库，跳过发布。按 README 完成 git init 与 remote 配置后可自动发布。")
        return False

    if dry_run:
        logger.info("[dry-run] 跳过 git 提交与推送")
        return False

    if not _run(["git", "add", "-A", "docs"], logger):
        return False

    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=str(ROOT), capture_output=True
    )
    if diff.returncode == 0:
        logger.info("docs/ 无变更，无需发布")
        return True

    if not _run(["git", "commit", "-m", message], logger):
        return False

    branch = deploy_cfg.get("branch", "main")
    remote = deploy_cfg.get("remote", "origin")
    pushed = False
    for attempt in range(3):
        if _run(["git", "push", remote, branch], logger):
            pushed = True
            break
        if attempt < 2:
            logger.warning("推送失败，%d 秒后重试（第 %d/3 次）", 6 * (attempt + 1), attempt + 1)
            time.sleep(6 * (attempt + 1))
    if not pushed:
        logger.error("三次推送均失败，网页未更新；本地产物已生成，可稍后手动 git push")
        return False

    logger.info("已推送到 %s/%s，Pages 将在数十秒内更新", remote, branch)
    return True
