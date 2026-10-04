from __future__ import annotations

import html
import json
import os
import shutil
from pathlib import Path

from .models import Channel, Validation
from .normalize import simple_category


def m3u_text(channels: list[Channel], epg_url: str, validations: dict[str, Validation]) -> str:
    escaped_epg = html.escape(epg_url, quote=True)
    lines = [f'#EXTM3U url-tvg="{escaped_epg}" x-tvg-url="{escaped_epg}"']
    for channel in sorted(channels, key=lambda c: (c.group, c.name, c.role)):
        attrs = {
            "tvg-id": channel.tvg_id,
            "tvg-name": channel.tvg_name or channel.name,
            "tvg-logo": channel.logo,
            "group-title": simple_category(channel),
            "tvg-language": channel.language,
            "owl-role": channel.role,
        }
        encoded = " ".join(f'{key}="{html.escape(value, quote=True)}"' for key, value in attrs.items() if value)
        quality = validations.get(channel.url, Validation())
        suffix = f" [{channel.role}]" if channel.role == "backup" else ""
        if quality.height:
            suffix += f" [{quality.height}p]"
        lines.extend([f"#EXTINF:-1 {encoded},{channel.name}{suffix}", channel.url])
    return "\n".join(lines) + "\n"


def sanity_check(stage: Path, minimum_channels: int) -> None:
    playlist = stage.joinpath("playlist.m3u").read_text(encoding="utf-8")
    if not playlist.startswith("#EXTM3U"):
        raise ValueError("playlist header missing")
    count = playlist.count("#EXTINF:")
    if count < minimum_channels:
        raise ValueError(f"sanity floor failed: {count} < {minimum_channels}")
    import xml.etree.ElementTree as ET
    ET.parse(stage / "epg.xml")
    stats = json.loads(stage.joinpath("stats.json").read_text(encoding="utf-8"))
    if stats.get("published_streams") != count:
        raise ValueError("stats/playlist count mismatch")


def atomic_publish(stage: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    old = destination.with_name(destination.name + ".old")
    if old.exists():
        shutil.rmtree(old)
    if destination.exists():
        os.replace(destination, old)
    try:
        os.replace(stage, destination)
    except Exception:
        if old.exists() and not destination.exists():
            os.replace(old, destination)
        raise
    if old.exists():
        shutil.rmtree(old)

