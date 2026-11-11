"""企业微信推送：走已授权的机器人（wecom-cli）或群机器人 webhook。"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time

import requests

from .util import circled

API = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"


def _byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def _cli(args: list[str], timeout: int = 40) -> dict:
    """直接调用 node 脚本，绕开 .cmd 的 cmd.exe 解析。

    消息正文里含 `>`（markdown 引用）等字符，走 .cmd 会被 cmd 当成重定向，
    曾因此把一行「> 今日收录…」写到磁盘上。用 node + js 入口最安全。
    """
    from pathlib import Path

    base = Path.home() / ".workbuddy/binaries/node/cli-connector-packages"
    js_entry = base / "node_modules/@wecom/cli/bin/wecom.js"
    node_exe = base / "node.exe"
    if not node_exe.exists():
        found = shutil.which("node")
        node_exe = Path(found) if found else node_exe

    if node_exe.exists() and js_entry.exists():
        cmd = [str(node_exe), str(js_entry), *args]
    else:
        cmd = [shutil.which("wecom-cli") or "wecom-cli", *args]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "wecom-cli 执行失败").strip()[:200])
    # 发送类命令成功时可能没有 stdout，不能直接 json.loads
    payload = (result.stdout or "").strip()
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {"raw": payload[:200]}


def resolve_chat(chat_name: str, logger: logging.Logger) -> tuple[str, str] | tuple[None, None]:
    """按会话名称匹配；找不到就退回第一个单聊。"""
    data = _cli(["message", "aibot", "sessions", "list"])
    sessions = data.get("sessions", [])
    for item in sessions:
        if chat_name and item.get("chat_name") == chat_name:
            return item["chat_id"], item["chat_name"]
    for item in sessions:
        if item.get("chat_type") == "single":
            return item["chat_id"], item["chat_name"]
    logger.warning("企业微信没有可发送的单聊会话")
    return None, None


def push_cli(
    items: list[dict],
    date_cn: str,
    overview: str,
    page_url: str,
    cfg: dict,
    logger: logging.Logger,
    dry_run: bool = False,
) -> bool:
    cli_cfg = cfg.get("push", {}).get("cli", {})
    if not cli_cfg.get("enabled", True):
        return False

    top_n = int(cfg.get("push", {}).get("wecom", {}).get("top_n", 5))
    max_bytes = int(cli_cfg.get("max_bytes", 18000))

    content = "\n\n".join(build_blocks(items[:top_n], date_cn, overview, page_url))
    if _byte_len(content) > max_bytes:
        content = content.encode("utf-8")[:max_bytes].decode("utf-8", "ignore") + "\n…"

    if dry_run:
        logger.info("[dry-run] 企微消息预览：\n%s", content)
        return True

    try:
        target = os.environ.get("WECOM_CHAT_NAME", "").strip() or str(cli_cfg.get("chat_name", ""))
        chat_id, chat_name = resolve_chat(target, logger)
        if not chat_id:
            return False
        _cli(
            [
                "message",
                "aibot",
                "send",
                "--json",
                json.dumps(
                    {"chat_id": chat_id, "msg_type": "markdown", "markdown": {"content": content}},
                    ensure_ascii=False,
                ),
            ]
        )
        logger.info("企业微信已发送给「%s」", chat_name)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("企业微信发送失败：%s", str(exc)[:200])
        return False


def build_blocks(items: list[dict], date_cn: str, overview: str, page_url: str) -> list[str]:
    head = [f"## AI 日报 · {date_cn}", f"> {overview}", ""]
    blocks = ["\n".join(head)]

    for idx, item in enumerate(items, 1):
        block = (
            f"**{circled(idx)} {item['title']}**\n"
            f"{item['summary']}\n"
            f"[阅读原文]({item['url']})"
        )
        blocks.append(block)

    if page_url:
        blocks.append(f"[查看完整日报 · 含开源榜与论文]({page_url})")
    return blocks


def pack_messages(blocks: list[str], max_bytes: int) -> list[str]:
    messages: list[str] = []
    current = ""
    for block in blocks:
        candidate = f"{current}\n\n{block}".strip() if current else block
        if _byte_len(candidate) <= max_bytes:
            current = candidate
            continue
        if current:
            messages.append(current)
        # 单块本身超限则硬切
        while _byte_len(block) > max_bytes:
            cut = block.encode("utf-8")[: max_bytes - 20].decode("utf-8", "ignore")
            messages.append(cut)
            block = block[len(cut) :]
        current = block
    if current:
        messages.append(current)
    return messages


def send(webhook: str, content: str, timeout: int = 15) -> dict:
    resp = requests.post(
        webhook,
        json={"msgtype": "markdown", "markdown": {"content": content}},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json()


def push(
    items: list[dict],
    date_cn: str,
    overview: str,
    page_url: str,
    cfg: dict,
    logger: logging.Logger,
    dry_run: bool = False,
) -> bool:
    wecom = cfg.get("push", {}).get("wecom", {})
    if not wecom.get("enabled", True):
        logger.info("企微推送已禁用，跳过")
        return False

    webhook = os.environ.get(wecom.get("webhook_env", "WECOM_WEBHOOK_URL"), "").strip()
    if not webhook:
        logger.warning("未配置 WECOM_WEBHOOK_URL，跳过推送")
        return False

    top_n = int(wecom.get("top_n", 5))
    max_bytes = int(wecom.get("max_bytes", 3500))
    interval = float(wecom.get("interval_sec", 3))

    blocks = build_blocks(items[:top_n], date_cn, overview, page_url)
    messages = pack_messages(blocks, max_bytes)
    logger.info("企微推送：%d 条消息，共 %d 字节", len(messages), sum(_byte_len(m) for m in messages))

    for idx, message in enumerate(messages, 1):
        if dry_run:
            logger.info("[dry-run] 第 %d 条预览：\n%s", idx, message)
            continue
        for attempt in range(3):
            try:
                result = send(webhook, message)
                if result.get("errcode") == 0:
                    logger.info("第 %d/%d 条发送成功", idx, len(messages))
                    break
                logger.error("第 %d 条返回错误：%s", idx, result)
                if result.get("errcode") in (45009,):  # 频率限制
                    time.sleep(20)
                    continue
                return False
            except Exception as exc:  # noqa: BLE001
                logger.warning("第 %d 条发送异常（第 %d 次）：%s", idx, attempt + 1, str(exc)[:100])
                time.sleep(3 * (attempt + 1))
        else:
            return False
        time.sleep(interval)

    return True
