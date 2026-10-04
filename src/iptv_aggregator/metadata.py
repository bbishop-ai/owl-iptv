from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import aiohttp

from .models import Channel
from .normalize import normalized_name

LANGUAGE_ALIASES = {"eng": "en", "spa": "es", "zho": "zh", "cmn": "zh", "fra": "fr", "deu": "de", "por": "pt", "rus": "ru", "ara": "ar", "jpn": "ja", "kor": "ko", "hin": "hi"}


class MetadataCatalog:
    """Authoritative IPTV-org channel metadata keyed by id and unique names."""

    def __init__(self, records: list[dict]):
        self.by_id = {str(row.get("id", "")).lower(): row for row in records if row.get("id")}
        names: dict[str, list[dict]] = defaultdict(list)
        for row in records:
            for value in [row.get("name", ""), *(row.get("alt_names") or [])]:
                key = normalized_name(value)
                if key:
                    names[key].append(row)
        self.by_name = {key: values[0] for key, values in names.items() if len({v.get("id") for v in values}) == 1}

    def enrich(self, channel: Channel) -> str:
        base_id = channel.tvg_id.split("@", 1)[0]
        row = self.by_id.get(base_id.lower()) if base_id else None
        method = "tvg-id"
        if row is None:
            row = self.by_name.get(normalized_name(channel.tvg_name or channel.name))
            method = "normalized-name"
        if row is None:
            return "none"
        channel.tvg_id = base_id or channel.tvg_id
        if not channel.tvg_id and row.get("id"):
            channel.tvg_id = str(row["id"])
        channel.country = str(row.get("country") or channel.country).upper()
        languages = row.get("languages") or []
        if languages:
            code = str(languages[0]).lower()
            channel.language = LANGUAGE_ALIASES.get(code, code[:2])
        channel.attrs["metadata-id"] = str(row.get("id", ""))
        channel.attrs["metadata-name"] = str(row.get("name", ""))
        channel.attrs["metadata-alt-names"] = json.dumps(row.get("alt_names") or [])
        return method


async def load_metadata(url: str, cache_path: Path, timeout_seconds: int = 30) -> tuple[MetadataCatalog, str]:
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "owl-iptv/1.0"}) as session:
            async with session.get(url, allow_redirects=True) as response:
                response.raise_for_status()
                raw = await response.read()
        records = json.loads(raw)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(raw)
        return MetadataCatalog(records), "fresh"
    except Exception as exc:
        if cache_path.exists():
            return MetadataCatalog(json.loads(cache_path.read_text(encoding="utf-8"))), f"stale-cache: {exc}"
        return MetadataCatalog([]), f"failed: {exc}"
