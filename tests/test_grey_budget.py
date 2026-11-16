from __future__ import annotations

from iptv_aggregator.dedupe import preselect_candidates
from iptv_aggregator.models import Channel


def _channel(source_id: str, url: str, tvg_id: str, logo: str = "") -> Channel:
    channel = Channel(name=tvg_id, url=url, source_id=source_id, tvg_id=tvg_id, country="US", language="en")
    channel.logo = logo
    return channel


def test_full_validation_budget_does_not_crowd_out_backup_overlay():
    licensed = [_channel("iptv-org", f"https://example.test/l{i}.m3u8", f"L{i}.us", "logo.png") for i in range(3)]
    grey = _channel("grey-sports", "https://example.test/g.m3u8", "grey.cardinals")
    selected, removed = preselect_candidates([*licensed, grey], 4, 3, {"grey-sports": 1}, {"grey-sports"})
    assert [c.source_id for c in selected].count("iptv-org") == 3
    assert grey in selected
    assert removed == 0


def test_budget_still_binds_licensed_sources():
    licensed = [_channel("iptv-org", f"https://example.test/l{i}.m3u8", f"L{i}.us", "logo.png") for i in range(3)]
    selected, removed = preselect_candidates(licensed, 4, 2, None, {"grey-sports"})
    assert len(selected) == 2
    assert removed == 1
