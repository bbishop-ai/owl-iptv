from __future__ import annotations

from iptv_aggregator.config import load_config
from iptv_aggregator.dedupe import preselect_candidates
from iptv_aggregator.models import Channel


def _channel(url: str, tvg_id: str, logo: str = "logo.png") -> Channel:
    channel = Channel(name=tvg_id, url=url, source_id="iptv-org", tvg_id=tvg_id, country="US", language="en")
    channel.logo = logo
    return channel


def test_http_only_priority_channel_survives_a_full_https_budget():
    # Plain-http ESPN sorts behind every HTTPS FAST channel; without priority
    # the validation budget fills before ESPN is ever probed.
    fast = [_channel(f"https://example.test/f{i}.m3u8", f"F{i}.us") for i in range(3)]
    espn = _channel("http://85.237.89.160:9590/usa-s/ESPN-HD/index.m3u8", "ESPN.us")
    selected, _ = preselect_candidates([*fast, espn], 4, 3)
    assert espn not in selected  # the bug this guards against
    selected, removed = preselect_candidates([*fast, espn], 4, 3, priority_tvg_ids={"ESPN.us"})
    assert espn in selected
    assert len(selected) == 3 and removed == 1


def test_priority_respects_per_identity_cap():
    rows = [_channel(f"http://example.test/e{i}.m3u8", "ESPN.us") for i in range(6)]
    selected, removed = preselect_candidates(rows, 4, 100, priority_tvg_ids={"ESPN.us"})
    assert len(selected) == 4 and removed == 2


def test_pipeline_config_prioritizes_espn_family():
    cfg = load_config("config/pipeline.yml")
    priority = set(cfg["validation"]["priority_tvg_ids"])
    assert {"ESPN.us", "ESPN2.us", "ESPNU.us", "ESPNews.us"} <= priority
