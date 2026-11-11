"""共享数据模型。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Article:
    title: str
    url: str
    source: str
    source_id: str
    published: datetime
    summary: str = ""
    category: str = "行业动态"
    score: float = 0.0
    lang: str = "zh"
    extra: dict = field(default_factory=dict)

    @property
    def url_hash(self) -> str:
        return hashlib.sha1(self.url.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "source_id": self.source_id,
            "published": self.published.isoformat(),
            "summary": self.summary,
            "category": self.category,
            "score": self.score,
            "extra": self.extra,
        }
