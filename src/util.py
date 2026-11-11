"""通用工具：时区、HTTP、文本清洗、日志。"""
from __future__ import annotations

import html
import logging
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent

try:  # Windows 上未必装了 tzdata，兜底用固定 +8
    from zoneinfo import ZoneInfo

    TZ = ZoneInfo("Asia/Shanghai")
except Exception:  # noqa: BLE001
    TZ = timezone(timedelta(hours=8))

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_KEEP_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")


def now_tz() -> datetime:
    return datetime.now(TZ)


def to_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


def strip_html(text: str) -> str:
    if not text:
        return ""
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def truncate(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，。、；：,. ") + "…"


def norm_title(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").lower()
    return _KEEP_RE.sub("", text)


def bigrams(text: str) -> set[str]:
    text = norm_title(text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def similarity(a: str, b: str) -> float:
    ga, gb = bigrams(a), bigrams(b)
    if not ga or not gb:
        return 0.0
    return len(ga & gb) / len(ga | gb)


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def normalize_url(url: str) -> str:
    """去掉追踪参数与末尾斜杠，用于跨源去重。"""
    if not url:
        return ""
    url = url.strip()
    url = re.sub(r"[?&](utm_[^=&]+|spm|from|source|ref|share_[^=&]+)=[^&]*", "", url)
    url = re.sub(r"[?&]$", "", url)
    url = url.rstrip("/")
    return url.split("#")[0]


def http_get(
    url: str,
    *,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 25,
    retries: int = 2,
    session: requests.Session | None = None,
) -> requests.Response:
    last: Exception | None = None
    hdrs = {**DEFAULT_HEADERS, **(headers or {})}
    for attempt in range(retries + 1):
        try:
            getter = session.get if session else requests.get
            resp = getter(url, params=params, headers=hdrs, timeout=timeout)
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"请求失败 {url}: {last}")


def load_env(path: Path) -> None:
    """极简 .env 解析，已存在的环境变量优先。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def setup_logger(log_dir: Path, name: str = "ai-daily") -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    logger.addHandler(stream)

    file_handler = logging.FileHandler(
        log_dir / f"{now_tz():%Y-%m-%d}.log", encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def circled(n: int) -> str:
    return CIRCLED[n - 1] if 1 <= n <= len(CIRCLED) else f"({n})"


def fmt_date_cn(dt: datetime) -> str:
    return f"{dt.year} 年 {dt.month} 月 {dt.day} 日"


def fmt_weekday_cn(dt: datetime) -> str:
    return "周" + "一二三四五六日"[dt.weekday()]


def fmt_hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")
