from __future__ import annotations

import re
from collections.abc import Iterable

from .models import Channel

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
URL_RE = re.compile(r"^(?:https?|rtmp|rtsp|udp|rtp)://", re.I)


def parse_playlist(text: str, source_id: str) -> list[Channel]:
    """Parse extended M3U and name,url text without executing provider directives."""
    text = text.lstrip("\ufeff")
    rows = [line.strip() for line in text.splitlines() if line.strip()]
    channels: list[Channel] = []
    pending: tuple[str, dict[str, str]] | None = None
    for row in rows:
        if row.startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(row))
            name = row.rsplit(",", 1)[-1].strip()
            pending = (name, attrs)
            continue
        if row.startswith("#"):
            continue
        if pending and URL_RE.match(row):
            name, attrs = pending
            channels.append(_channel(name, row, source_id, attrs))
            pending = None
            continue
        if "," in row:
            name, url = row.split(",", 1)
            if URL_RE.match(url.strip()):
                channels.append(_channel(name.strip(), url.strip(), source_id, {}))
    return channels


def _channel(name: str, url: str, source_id: str, attrs: dict[str, str]) -> Channel:
    return Channel(
        name=name or attrs.get("tvg-name", "Unnamed"),
        url=url,
        source_id=source_id,
        tvg_id=attrs.get("tvg-id", ""),
        tvg_name=attrs.get("tvg-name", ""),
        logo=attrs.get("tvg-logo", ""),
        group=attrs.get("group-title", ""),
        language=attrs.get("tvg-language", attrs.get("language", "unknown")),
        country=attrs.get("tvg-country", attrs.get("country", "")),
        attrs=attrs,
    )


def iter_urls(channels: Iterable[Channel]) -> Iterable[str]:
    return (channel.url for channel in channels)

