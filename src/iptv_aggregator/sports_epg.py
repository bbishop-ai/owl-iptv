"""Conservative sports event → XMLTV programme adapter.

Injects build-time SportsEvent records as XMLTV <programme> elements into an
existing epg.xml byte string.  Only primary channels with a non-empty tvg-id
that can be uniquely matched to a scheduled broadcast network receive entries.
Upstream programmes are never modified; the playlist (M3U) is never touched.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from .models import Channel
from .normalize import normalized_name
from .sports_guide import SportsEvent

# ---------------------------------------------------------------------------
# Duration estimates by league (no ESPN end-time available).
# Upper-bound values; description text clearly marks them as estimated.
# ---------------------------------------------------------------------------
_SPORT_DURATION_MINUTES: dict[str, int] = {
    "College Football": 240,
    "NFL": 210,
    "MLB": 180,
    "NBA": 150,
    "NHL": 180,
}
_DEFAULT_DURATION_MINUTES = 180

# ---------------------------------------------------------------------------
# Broadcast names that must never produce XMLTV entries.
# Covers streaming-only services, pending placeholders, and RSN labels that
# have no unique channel identity in a typical playlist.
#
# ABC, CBS, NBC, and Fox are NOT unconditionally skipped: they are eligible
# when exactly one primary channel's display name maps to that key.  If
# multiple channels produce the same normalized key (ambiguous), the event is
# counted under sports_epg_skip_ambiguous and no entry is created.
#
# Checked using raw case-fold (whitespace-collapsed, no punctuation removal)
# so that "ESPN+" and "Paramount+" are not confused with "ESPN"/"Paramount".
# ---------------------------------------------------------------------------
_SKIP_BROADCASTS: frozenset[str] = frozenset({
    "broadcast pending",
    "espn+",
    "espn plus",
    "espnplus",
    "prime video",
    "prime video (local)",  # raw casefold; parens preserved by _raw_lower
    "primevideo",
    "peacock",
    "paramount+",
    "paramountplus",
    "max",
})


def _raw_lower(value: str) -> str:
    """Case-fold with whitespace collapse; preserves punctuation like '+' and '('."""
    return re.sub(r"\s+", " ", value.strip().casefold())

# ---------------------------------------------------------------------------
# Explicit broadcast-name aliases.
# Key:   normalized ESPN broadcast name (what ESPN data contains).
# Value: normalized channel display name to look up in the playlist.
# Extend ONLY after auditing actual playlist data.
# ---------------------------------------------------------------------------
_BROADCAST_ALIASES: dict[str, str] = {
    "nfl net": "nfl network",
    "btn": "big ten network",
    "cbssn": "cbs sports network",
    # ESPN often emits "ESPNU"; playlist channels may be named "ESPN U"
    "espnu": "espn u",
    # ESPN often emits "ESPNews"; playlist channels may be named "ESPN News"
    "espnews": "espn news",
}


def _channel_key(name: str) -> str:
    """Quality-stripped key for channel display names.

    Removes resolution/quality suffixes so "ESPN (720p)" and "ESPN" both
    produce the key "espn".
    """
    from .epg import quality_stripped
    return quality_stripped(name)


def _xmltv_time(dt: datetime) -> str:
    utc = dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    return utc.strftime("%Y%m%d%H%M%S +0000")


def _estimated_stop(event: SportsEvent) -> tuple[datetime, bool]:
    minutes = _SPORT_DURATION_MINUTES.get(event.league, _DEFAULT_DURATION_MINUTES)
    return event.start_utc + timedelta(minutes=minutes), True


def _build_channel_map(channels: list[Channel]) -> dict[str, list[str]]:
    """Map normalized channel display name → list of tvg_ids (primary only)."""
    result: dict[str, list[str]] = defaultdict(list)
    for channel in channels:
        if channel.role != "primary" or not channel.tvg_id:
            continue
        key = _channel_key(channel.name)
        if key:
            result[key].append(channel.tvg_id)
    return dict(result)



def _make_programme(
    tvg_id: str,
    event: SportsEvent,
    broadcast: str,
    stop_utc: datetime,
    is_estimated: bool,
) -> ET.Element:
    title = f"{event.away} at {event.home}"
    prog = ET.Element("programme", {
        "start": _xmltv_time(event.start_utc),
        "stop": _xmltv_time(stop_utc),
        "channel": tvg_id,
    })
    title_el = ET.SubElement(prog, "title", {"lang": "en"})
    title_el.text = title

    sub = ET.SubElement(prog, "sub-title", {"lang": "en"})
    sub.text = event.league

    desc_parts = [f"{event.league} · {title}"]
    if broadcast.strip():
        desc_parts.append(broadcast)
    if is_estimated:
        mins = _SPORT_DURATION_MINUTES.get(event.league, _DEFAULT_DURATION_MINUTES)
        h, m = divmod(mins, 60)
        duration_str = f"{h}h" + (f" {m}m" if m else "")
        desc_parts.append(f"Stop time is an estimate ({duration_str})")
    desc_parts.append("Schedule listing does not guarantee a playable stream.")
    desc_el = ET.SubElement(prog, "desc", {"lang": "en"})
    desc_el.text = " · ".join(desc_parts)

    cat = ET.SubElement(prog, "category", {"lang": "en"})
    cat.text = "Sports"
    return prog


def inject_sports_epg(
    xmltv_data: bytes,
    events: list[SportsEvent],
    channels: list[Channel],
) -> tuple[bytes, dict[str, int]]:
    """Inject sports event programmes into an XMLTV byte string.

    Parses xmltv_data, appends validated injected programmes, and re-serializes.
    Upstream programme elements are never modified or removed.
    The M3U and channel tvg-ids are never touched.

    Returns (new_xmltv_bytes, stats) where stats contains counters for added
    programmes and each skip reason.
    """
    stats: dict[str, int] = {
        "sports_events_fetched": len(events),
        "sports_epg_programmes_added": 0,
        "sports_epg_skip_unmapped": 0,
        "sports_epg_skip_ambiguous": 0,
        "sports_epg_skip_already_present": 0,
        "sports_epg_skip_incomplete": 0,
        "sports_epg_skip_broadcast": 0,
    }

    try:
        root = ET.fromstring(xmltv_data)
    except ET.ParseError:
        return xmltv_data, stats

    # Index existing upstream programmes for deduplication.
    # Key: (channel_id, xmltv_start_string, normalized_title)
    existing_keys: set[tuple[str, str, str]] = set()
    for prog in root.findall("programme"):
        ch_id = prog.get("channel", "")
        start = prog.get("start", "")
        title_el = prog.find("title")
        norm_title = normalized_name(title_el.text or "") if title_el is not None else ""
        if ch_id and start:
            existing_keys.add((ch_id, start, norm_title))

    channel_map = _build_channel_map(channels)

    for event in events:
        if not event.start_utc:
            stats["sports_epg_skip_incomplete"] += 1
            continue

        stop_utc, is_estimated = _estimated_stop(event)
        start_xmltv = _xmltv_time(event.start_utc)
        norm_title = normalized_name(f"{event.away} at {event.home}")

        # Track tvg_ids already injected for this event to avoid duplication
        # when the same channel appears via two broadcast aliases.
        injected_for_event: set[str] = set()

        for broadcast in event.broadcasts:
            if not broadcast.strip():
                stats["sports_epg_skip_incomplete"] += 1
                continue

            # Skip list checked on raw casefold so "ESPN+" is not confused with "ESPN"
            if _raw_lower(broadcast) in _SKIP_BROADCASTS:
                stats["sports_epg_skip_broadcast"] += 1
                continue

            norm_b = normalized_name(broadcast)
            if not norm_b:
                stats["sports_epg_skip_incomplete"] += 1
                continue

            # Resolve broadcast to a unique tvg_id
            if norm_b in channel_map:
                ids = channel_map[norm_b]
                if len(ids) != 1:
                    stats["sports_epg_skip_ambiguous"] += 1
                    continue
                tvg_id = ids[0]
            else:
                alias = _BROADCAST_ALIASES.get(norm_b)
                if alias and alias in channel_map:
                    ids = channel_map[alias]
                    if len(ids) != 1:
                        stats["sports_epg_skip_ambiguous"] += 1
                        continue
                    tvg_id = ids[0]
                else:
                    stats["sports_epg_skip_unmapped"] += 1
                    continue

            # Skip if this event was already injected for this channel
            if tvg_id in injected_for_event:
                continue

            # Deduplicate against upstream programmes
            key = (tvg_id, start_xmltv, norm_title)
            if key in existing_keys:
                stats["sports_epg_skip_already_present"] += 1
                continue

            prog = _make_programme(tvg_id, event, broadcast, stop_utc, is_estimated)
            root.append(prog)
            existing_keys.add(key)
            injected_for_event.add(tvg_id)
            stats["sports_epg_programmes_added"] += 1

    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue(), stats
