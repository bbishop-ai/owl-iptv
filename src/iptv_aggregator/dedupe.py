from __future__ import annotations

from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Channel, Validation

VOLATILE_QUERY = {"token", "auth", "expires", "expire", "timestamp", "sign", "signature"}


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in VOLATILE_QUERY]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def rank(channel: Channel, validation: Validation, preferred_countries: set[str] | None = None) -> tuple:
    pixels = (validation.width or 0) * (validation.height or 0)
    latency = validation.latency_ms if validation.latency_ms is not None else 999_999
    country_preferred = channel.country.upper() in (preferred_countries or set())
    return (validation.ok, validation.frozen is not True, country_preferred, pixels, validation.height or 0, validation.fps or 0, channel.url.startswith("https://"), -latency)


def preselect_candidates(
    channels: list[Channel],
    per_identity: int,
    total_limit: int,
    per_source: dict[str, int] | None = None,
) -> tuple[list[Channel], int]:
    """Remove exact URLs and bound validation while retaining source diversity per identity."""
    seen_urls: set[str] = set()
    counts: dict[str, int] = defaultdict(int)
    selected: list[Channel] = []
    removed = 0
    # Prefer HTTPS and entries with EPG/logo metadata before media quality is known.
    ordered = sorted(channels, key=lambda c: (bool(c.tvg_id), bool(c.logo), c.url.startswith("https://")), reverse=True)
    for channel in ordered:
        key = canonical_url(channel.url)
        source_limit = (per_source or {}).get(channel.source_id, per_identity)
        if key in seen_urls or counts[channel.identity] >= source_limit or len(selected) >= total_limit:
            removed += 1
            continue
        seen_urls.add(key)
        counts[channel.identity] += 1
        selected.append(channel)
    return selected, removed


def select_streams(
    channels: list[Channel],
    validations: dict[str, Validation],
    backups: int = 1,
    preferred_countries: list[str] | None = None,
    backups_by_source: dict[str, int] | None = None,
):
    exact_seen: set[str] = set()
    by_identity: dict[str, list[Channel]] = defaultdict(list)
    exact_removed = 0
    for channel in channels:
        url_key = canonical_url(channel.url)
        if url_key in exact_seen:
            exact_removed += 1
            continue
        exact_seen.add(url_key)
        by_identity[channel.identity].append(channel)
    selected: list[Channel] = []
    collapsed = 0
    for candidates in by_identity.values():
        viable = [c for c in candidates if validations.get(c.url, Validation()).ok]
        preferred = {value.upper() for value in (preferred_countries or [])}
        viable.sort(key=lambda c: rank(c, validations[c.url], preferred), reverse=True)
        source_limits = {
            source_id: 1 + (backups_by_source or {}).get(source_id, backups)
            for source_id in {c.source_id for c in candidates}
        }
        keep = []
        source_counts: dict[str, int] = defaultdict(int)
        for channel in viable:
            if source_counts[channel.source_id] >= source_limits[channel.source_id]:
                continue
            keep.append(channel)
            source_counts[channel.source_id] += 1
        for index, channel in enumerate(keep):
            channel.role = "primary" if index == 0 else "backup"
            selected.append(channel)
        collapsed += max(0, len(candidates) - len(keep))
    return selected, {"exact_url_duplicates_removed": exact_removed, "identity_duplicates_removed": collapsed}
