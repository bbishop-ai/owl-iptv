from __future__ import annotations

from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Channel, Validation

VOLATILE_QUERY = {"token", "auth", "expires", "expire", "timestamp", "sign", "signature"}


def canonical_url(url: str) -> str:
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in VOLATILE_QUERY]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), ""))


def identity_key(channel: Channel) -> str:
    """Primary grouping key: tvg_id > normalized name + country."""
    if channel.tvg_id:
        return f"tvg:{channel.tvg_id}"
    return f"name:{channel.identity}"


def rank(
    channel: Channel,
    validation: Validation,
    preferred_countries: set[str] | None = None,
    provider_preference: dict[str, int] | None = None,
) -> tuple:
    # Reliability is the primary quality signal (0-100)
    reliability = validation.reliability if validation.reliability is not None else 0
    pixels = (validation.width or 0) * (validation.height or 0)
    latency = validation.latency_ms if validation.latency_ms is not None else 999_999
    country_preferred = channel.country.upper() in (preferred_countries or set())
    provider_pref = (provider_preference or {}).get(channel.source_id, 0)

    return (
        validation.ok,
        validation.frozen is not True,
        reliability,  # Primary: rolling reliability score
        provider_pref,  # Secondary: provider preference (DNA)
        country_preferred,  # Tertiary: country preference
        pixels,  # Resolution
        validation.height or 0,
        validation.fps or 0,
        channel.url.startswith("https://"),
        -latency,
    )


def preselect_candidates(
    channels: list[Channel],
    per_identity: int,
    total_limit: int,
    per_source: dict[str, int] | None = None,
    unbudgeted_sources: set[str] | None = None,
) -> tuple[list[Channel], int]:
    """Remove exact URLs and bound validation while retaining source diversity per identity.

    Channels from ``unbudgeted_sources`` (additive backup overlays) are still
    capped per identity but do not consume ``total_limit``, so a full validation
    budget cannot crowd them out.
    """
    budgeted = 0
    seen_urls: set[str] = set()
    counts: dict[str, int] = defaultdict(int)
    source_counts: dict[tuple[str, str], int] = defaultdict(int)
    selected: list[Channel] = []
    removed = 0
    # Prefer HTTPS and entries with EPG/logo metadata before media quality is known.
    ordered = sorted(channels, key=lambda c: (bool(c.tvg_id), bool(c.logo), c.url.startswith("https://")), reverse=True)
    for channel in ordered:
        key = canonical_url(channel.url)
        identity = identity_key(channel)
        source_limit = (per_source or {}).get(channel.source_id, per_identity)
        if source_limit > per_identity:
            # Expansion (e.g. a dense provider): bounded by the shared identity count.
            over = counts[identity] >= source_limit
        else:
            # Cap (e.g. the grey overlay): bounded by this source's own count per identity.
            over = counts[identity] >= per_identity or source_counts[(channel.source_id, identity)] >= source_limit
        counts_budget = channel.source_id not in (unbudgeted_sources or set())
        if key in seen_urls or over or (counts_budget and budgeted >= total_limit):
            removed += 1
            continue
        budgeted += counts_budget
        seen_urls.add(key)
        counts[identity] += 1
        source_counts[(channel.source_id, identity)] += 1
        selected.append(channel)
    return selected, removed


def select_streams(
    channels: list[Channel],
    validations: dict[str, Validation],
    backups: int = 1,
    preferred_countries: list[str] | None = None,
    backups_by_source: dict[str, int] | None = None,
    provider_preference: dict[str, int] | None = None,
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
        by_identity[identity_key(channel)].append(channel)
    selected: list[Channel] = []
    collapsed = 0
    preferred = {value.upper() for value in (preferred_countries or [])}
    for candidates in by_identity.values():
        viable = [c for c in candidates if validations.get(c.url, Validation()).ok]
        viable.sort(key=lambda c: rank(c, validations[c.url], preferred, provider_preference), reverse=True)
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
