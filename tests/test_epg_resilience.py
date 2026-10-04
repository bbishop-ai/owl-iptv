"""Retry + stale-cache resilience in fetch_epg (added 2026-10-04).

Background: China-hosted guides truncated mid-download from US CI runners
(ContentLengthError/TransferEncodingError); fetch_epg had no retry, so one
truncated read silently dropped a whole guide's programmes from that run's
published epg.xml. These tests pin the replacement behavior: reject truncated
gzip bodies, retry, fall back to the last complete body, and never cache a
truncated body over a good one.
"""
from __future__ import annotations

import gzip
import io
import json
from pathlib import Path

import aiohttp
import pytest

from iptv_aggregator.epg import EPGData, fetch_epg, _gzip_complete
from iptv_aggregator.models import Channel
from iptv_aggregator.pipeline import _unmatched_channels


GUIDE_XML = (
    '<tv generator-info-name="test">'
    '<channel id="one.us"><display-name>Example TV</display-name></channel>'
    '<programme start="20261004000000 +0000" stop="20261004010000 +0000" channel="one.us">'
    "<title>Evening news</title></programme>"
    "</tv>"
).encode("utf-8")


class _FakeResponse:
    def __init__(self, payload: bytes, status: int = 200):
        self._payload = payload
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(None, (), status=self.status)

    async def read(self) -> bytes:
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FlakySession:
    """Returns truncated on the first len(calls_by_path) calls per path."""

    def __init__(self, good: bytes, truncated: bytes, truncated_first: int = 1):
        self.good = good
        self.truncated = truncated
        self.calls_by_path: dict[str, int] = {}
        self.truncated_first = truncated_first

    def get(self, url: str, **kwargs):
        path = url  # each test uses a distinct fake path
        count = self.calls_by_path.get(path, 0)
        self.calls_by_path[path] = count + 1
        payload = self.truncated if count < self.truncated_first else self.good
        return _FakeResponse(payload)


def test_gzip_complete_detects_truncation():
    good = gzip.compress(GUIDE_XML)
    assert _gzip_complete(good)
    # Truncated middle: header + partial body, no trailer.
    assert not _gzip_complete(good[: len(good) // 2])


def install_fake_session(monkeypatch, session):
    class _FakeSessionCtx:
        async def __aenter__(self):
            return session
        async def __aexit__(self, *exc):
            return False
    def fake_session(*args, **kwargs):
        return _FakeSessionCtx()
    monkeypatch.setattr(aiohttp, "ClientSession", fake_session)


@pytest.mark.asyncio
async def test_fetch_epg_retries_and_recovers(monkeypatch):
    good = gzip.compress(GUIDE_XML)
    session = _FlakySession(good, good[: len(good) // 2], truncated_first=1)
    install_fake_session(monkeypatch, session)
    source = {"id": "guide", "url": "https://flaky.test/guide.xml.gz", "kind": "epg", "enabled": True}
    sleepcalls: list[float] = []
    import iptv_aggregator.epg as epg_mod
    async def nosleep(_):
        sleepcalls.append(_)
    monkeypatch.setattr(epg_mod.asyncio, "sleep", nosleep)
    data, statuses = await fetch_epg([source], timeout_seconds=5)
    assert statuses["guide"] == "succeeded"
    assert session.calls_by_path["https://flaky.test/guide.xml.gz"] >= 2  # retried
    assert "one.us" in data.channels


@pytest.mark.asyncio
async def test_fetch_epg_falls_back_to_last_good_body(monkeypatch, tmp_path: Path):
    good = gzip.compress(GUIDE_XML)
    cache_dir = tmp_path / "epg-cache"
    cache_dir.mkdir()
    (cache_dir / "guide.body").write_bytes(good)
    # Always truncates now.
    session = _FlakySession(good, good[: 10], truncated_first=999)
    install_fake_session(monkeypatch, session)
    import iptv_aggregator.epg as epg_mod
    async def nosleep(_):
        pass
    monkeypatch.setattr(epg_mod.asyncio, "sleep", nosleep)
    source = {"id": "guide", "url": "https://flaky.test/guide.xml.gz", "kind": "epg", "enabled": True}
    data, statuses = await fetch_epg([source], timeout_seconds=5, stale_cache_dir=cache_dir)
    assert statuses["guide"].startswith("stale-cache:"), statuses
    assert "one.us" in data.channels  # guide data survived the outage


@pytest.mark.asyncio
async def test_fetch_epg_does_not_poison_cache_with_truncated_body(monkeypatch, tmp_path: Path):
    good = gzip.compress(GUIDE_XML)
    cache_dir = tmp_path / "epg-cache"
    cache_dir.mkdir()
    # Pre-populate good body; this run truncates. The cache must keep the good bytes.
    (cache_dir / "guide.body").write_bytes(good)
    session = _FlakySession(good, good[: 64], truncated_first=999)
    install_fake_session(monkeypatch, session)
    import iptv_aggregator.epg as epg_mod
    async def nosleep(_):
        pass
    monkeypatch.setattr(epg_mod.asyncio, "sleep", nosleep)
    source = {"id": "guide", "url": "https://flaky.test/guide.xml.gz", "kind": "epg", "enabled": True}
    await fetch_epg([source], timeout_seconds=5, stale_cache_dir=cache_dir)
    assert (cache_dir / "guide.body").read_bytes() == good


def test_unmatched_channels_export_shape():
    channel = Channel("Nowhere TV", "https://host.test/live", "src", tvg_id="")
    epg = EPGData()
    out = _unmatched_channels([channel], epg)
    assert out["unmatched"] == 1
    row = out["channels"][0]
    assert row["name"] == "Nowhere TV"
    assert "tvg_id" in row and "alt_names" in row and "url_host" in row
    # Matched channels are excluded.
    channel2 = Channel("Matched TV", "https://host2.test/live", "src", tvg_id="matched.us")
    epg.channels["matched.us"] = None  # presence in epg.channels = matched
    out2 = _unmatched_channels([channel2], epg)
    assert out2["unmatched"] == 0