from __future__ import annotations

import asyncio
import gzip
import io
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from rapidfuzz.fuzz import ratio, token_sort_ratio

from .models import Channel
from .normalize import normalized_name


JUNK_WORDS = re.compile(r"\b(?:360p|480p|576p|216p|uhd|fhd|hd|sd|4k|8k|backup|mirror|not 24 7)\b")


def quality_stripped(name: str) -> str:
    """normalized_name with resolution/quality/availability junk removed (join-only)."""
    return re.sub(r"\s+", " ", JUNK_WORDS.sub(" ", normalized_name(name))).strip()


@dataclass
class EPGData:
    channels: dict[str, ET.Element] = field(default_factory=dict)
    programmes: dict[str, list[ET.Element]] = field(default_factory=lambda: defaultdict(list))
    channel_source: dict[str, str] = field(default_factory=dict)


def normalized_epg_id(value: str) -> str:
    """Collapse known XMLTV provider suffixes without guessing across channels."""
    value = value.strip().casefold().split("@", 1)[0]
    is_local = bool(re.search(r"\.us_locals\d+$", value))
    value = re.sub(r"\.us_locals\d+$", ".us", value)
    value = re.sub(r"(\.[a-z]{2})\d+$", r"\1", value)
    if is_local:
        value = re.sub(r"-(?:d|dt|ld|cd|tv)(?=\.us$)", "", value)
    return value


async def fetch_epg(sources: list[dict[str, Any]], timeout_seconds: int = 45, programme_ids: set[str] | None = None, programme_names: set[str] | None = None) -> tuple[EPGData, dict[str, str]]:
    enabled = [s for s in sources if s.get("enabled", True) and s.get("kind") == "epg"]
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    statuses: dict[str, str] = {}
    data = EPGData()
    async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "owl-iptv/1.0"}) as session:
        async def one(source):
            try:
                async with session.get(source["url"], allow_redirects=True) as response:
                    response.raise_for_status()
                    raw = await response.read()
                stream = gzip.GzipFile(fileobj=io.BytesIO(raw)) if raw[:2] == b"\x1f\x8b" else io.BytesIO(raw)
                parsed = EPGData()
                wanted_ids = set(programme_ids or set())
                wanted_id_keys = {normalized_epg_id(value) for value in wanted_ids}
                root = None
                for event, element in ET.iterparse(stream, events=("start", "end")):
                    if event == "start" and root is None:
                        root = element
                    if event != "end":
                        continue
                    if element.tag == "channel" and element.get("id"):
                        epg_id = element.get("id", "")
                        parsed.channels[epg_id] = element
                        if normalized_epg_id(epg_id) in wanted_id_keys:
                            wanted_ids.add(epg_id)
                        if any(normalized_name(display.text or "") in (programme_names or set()) for display in element.findall("display-name")):
                            wanted_ids.add(epg_id)
                    elif element.tag == "programme" and element.get("channel") in wanted_ids:
                        parsed.programmes[element.get("channel", "")].append(element)
                    elif element.tag == "programme":
                        element.clear()
                    if root is not None and element.tag in {"channel", "programme"}:
                        root.clear()
                return source, parsed, "succeeded"
            except Exception as exc:
                return source, None, f"failed: {exc}"
        results = await asyncio.gather(*(one(source) for source in enabled))
    for source, parsed, status in sorted(results, key=lambda r: _epg_source_rank(r[0])):
        statuses[source["id"]] = status
        if parsed is None:
            continue
        for channel_id, element in parsed.channels.items():
            if channel_id and channel_id not in data.channels:
                data.channels[channel_id] = element
                data.channel_source[channel_id] = source["id"]
        for channel_id, programmes in parsed.programmes.items():
            data.programmes[channel_id].extend(programmes)
    return data, statuses


def _url_provider_hex(url: str) -> tuple[str | None, str | None]:
    """(provider, provider-native id) from jmp2.uk short links, else (None, None)."""
    m = re.search(r"jmp2\.uk/([a-z]{3})-([0-9a-f]{16,32})", url or "")
    if not m:
        return None, None
    return {"plu": "pluto", "plx": "plex", "sam": "samsung", "rok": "roku", "tub": "tubi", "xum": "xumo"}.get(m.group(1)), m.group(2)


def _epg_source_rank(source: dict[str, Any]) -> int:
    """Guide priority: authoritative per-provider guides first, generic last."""
    url = str(source.get("url", ""))
    if "i.mjh.nz" in url:
        return 0
    if "BuddyChewChew" in url:
        return 1
    if "epgshare01" in url:
        return 2
    if "vcicio" in url:
        return 3
    if "onrender.com" in url:
        return 4
    return 5


def _epg_id_region(epg_id: str) -> str | None:
    """Trailing region code of an XMLTV id (iptv-org style: Name.us2 -> us)."""
    m = re.search(r"\.([a-z]{2})\d*$", epg_id or "")
    return m.group(1) if m else None


def match_channels(channels: list[Channel], epg: EPGData, fuzzy_threshold: int = 96):
    """Match channels to EPG ids.

    Strategy: per-guide matching in priority order. A name that is unique
    within one guide is a confident join even when a low-priority guide reuses
    the same name for an unrelated channel (global-ambiguity matching would
    block these joins, and did: coverage fell 27.5% -> 18% when the guides were
    pooled globally).
    """
    stats = {"epg_exact_id": 0, "epg_normalized_id": 0, "epg_exact_name": 0, "epg_fuzzy": 0, "epg_url_id": 0, "epg_unmatched": 0}

    # Pass 0: URL-embedded provider ids (zero ambiguity — provider's own id space).
    unassigned = []
    for channel in channels:
        if channel.tvg_id in epg.channels:
            stats["epg_exact_id"] += 1
            continue
        _, hex_id = _url_provider_hex(channel.url)
        if hex_id and hex_id in epg.channels:
            channel.tvg_id = hex_id
            stats["epg_url_id"] += 1
            continue
        unassigned.append(channel)

    # Group guide channels by source, highest-priority guide first. EPGData
    # built without source attribution (e.g. hand-assembled in tests) is
    # matched as a single trailing orphan group.
    by_source: dict[str, list[str]] = defaultdict(list)
    for epg_id, src in epg.channel_source.items():
        by_source[src].append(epg_id)
    orphan_ids = [epg_id for epg_id in epg.channels if epg_id not in epg.channel_source]
    if orphan_ids:
        by_source[""] = orphan_ids

    def name_variants(value: str) -> set[str]:
        out = set()
        if value:
            out.add(normalized_name(value))
            out.add(quality_stripped(value))
        out.discard("")
        return out

    for src, epg_ids in by_source.items():
        names: dict[str, list[str]] = defaultdict(list)
        ids: dict[str, list[str]] = defaultdict(list)
        for epg_id in epg_ids:
            element = epg.channels.get(epg_id)
            if element is None:
                continue
            ids[normalized_epg_id(epg_id)].append(epg_id)
            for display in element.findall("display-name"):
                raw = normalized_name(display.text or "")
                if raw:
                    names[raw].append(epg_id)
                stripped = quality_stripped(display.text or "")
                if stripped and stripped != raw:
                    names[stripped].append(epg_id)
        for channel in unassigned:
            if channel.tvg_id in epg.channels:
                continue
            # Region guard: a channel already carries a country-coded tvg-id
            # (e.g. Oxygen.us); a same-name guide entry for a different region
            # (Oxygen.au) is a different channel, not a match.
            want_region = _epg_id_region(channel.tvg_id) if channel.tvg_id else None
            normalized_ids = ids.get(normalized_epg_id(channel.tvg_id), []) if channel.tvg_id else []
            if len(normalized_ids) == 1:
                channel.tvg_id = normalized_ids[0]
                stats["epg_normalized_id"] += 1
                continue
            try:
                alt_names = json.loads(channel.attrs.get("metadata-alt-names", "[]"))
            except json.JSONDecodeError:
                alt_names = []
            keys = set()
            for value in [channel.tvg_name, channel.name, channel.attrs.get("metadata-name", ""), *alt_names]:
                keys |= name_variants(value)
            exact_ids = {ids[0] for key in keys for ids in [names.get(key, [])] if len(ids) == 1}
            if want_region:
                # Only reject when the guide id *carries* a different region;
                # region-less ids (distro/hex namespaces) can't conflict.
                exact_ids = {eid for eid in exact_ids if _epg_id_region(eid) in (None, want_region)}
            if len(exact_ids) == 1:
                channel.tvg_id = exact_ids.pop()
                stats["epg_exact_name"] += 1
                continue
            # Fuzzy: word-order-insensitive, unique best target within this guide.
            scored = sorted(((max((token_sort_ratio(key, candidate) for key in keys), default=0), ids) for candidate, ids in names.items()), reverse=True)
            if scored and scored[0][0] >= fuzzy_threshold and len(scored[0][1]) == 1 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
                candidate_id = scored[0][1][0]
                if want_region is None or _epg_id_region(candidate_id) in (None, want_region):
                    channel.tvg_id = candidate_id
                    stats["epg_fuzzy"] += 1
    for channel in unassigned:
        if channel.tvg_id not in epg.channels:
            stats["epg_unmatched"] += 1
    return stats


def xmltv_bytes(channels: list[Channel], epg: EPGData) -> bytes:
    root = ET.Element("tv", {"generator-info-name": "owl-iptv"})
    ids = {channel.tvg_id for channel in channels if channel.tvg_id}
    for epg_id in sorted(ids):
        if epg_id in epg.channels:
            root.append(epg.channels[epg_id])
        for programme in epg.programmes.get(epg_id, []):
            root.append(programme)
    buffer = io.BytesIO()
    ET.ElementTree(root).write(buffer, encoding="utf-8", xml_declaration=True)
    return buffer.getvalue()
