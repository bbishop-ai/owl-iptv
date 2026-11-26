"""Soft-policy must also convert CACHED failed results, not just fresh probes.

Regression for the #19 deploy: previously-failed URLs sit in the persistent
validation cache; on the next run they short-circuit as cached hits BEFORE
the inline soft conversion runs (it only wraps freshly-probed channels), so
toggling soft_validation on a source had zero effect until TTL expiry.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from iptv_aggregator.models import Channel
from iptv_aggregator.validator import Validator


def _channel(url: str) -> Channel:
    ch = Channel(name="Tubi Test", url=url, source_id="f30873f60166")
    ch.language = "en"
    ch.country = "US"
    return ch


def _seed_cache(path: Path, url: str) -> None:
    path.write_text(json.dumps({url: {
        "ok": False,
        "error": "HTTP 403",
        "checked_at": "2026-11-26T20:00:00+00:00",
    }}))


@pytest.mark.asyncio
async def test_cached_failure_converts_via_soft_policy(tmp_path):
    url = "https://live-manifest.production-public.tubi.io/live/x/playlist.m3u8"
    cache = tmp_path / "cache.json"
    _seed_cache(cache, url)
    validator = Validator(cache, timeout=5, concurrency=4, ttl_hours=6, deep_limit=0)

    async def fake_probe(session, channel):  # never called; cached path only
        raise AssertionError("cached entry must not be re-probed")

    validator._http_probe = fake_probe  # type: ignore[method-assign]

    results = await validator.validate([_channel(url)], skip_urls=set(),
                                       soft_urls={url})
    value = results[url]
    assert value.ok is True
    assert "soft-kept" in value.error
    assert "HTTP 403" in value.error

    persisted = json.loads(cache.read_text())[url]
    assert persisted["ok"] is True, "converted verdict must be written back"


@pytest.mark.asyncio
async def test_definitive_death_stays_failed_even_when_soft(tmp_path):
    url = "https://example.org/dead.m3u8"
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({url: {
        "ok": False, "error": "HTTP 404",
        "checked_at": "2026-11-26T20:00:00+00:00",
    }}))
    validator = Validator(cache, timeout=5, concurrency=4, ttl_hours=6, deep_limit=0)
    results = await validator.validate([_channel(url)], skip_urls=set(), soft_urls={url})
    assert results[url].ok is False
