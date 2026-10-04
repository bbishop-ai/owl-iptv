from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class Channel:
    name: str
    url: str
    source_id: str
    tvg_id: str = ""
    tvg_name: str = ""
    logo: str = ""
    group: str = ""
    language: str = "unknown"
    country: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    identity: str = ""
    role: str = "primary"


@dataclass(slots=True)
class Validation:
    ok: bool = False
    latency_ms: int | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    frozen: bool | None = None
    checked_at: str = ""
    error: str = ""
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

