from __future__ import annotations

import json

import pytest

from iptv_aggregator.dedupe import preselect_candidates, select_streams
from iptv_aggregator.models import Channel, Validation
from iptv_aggregator.publish import m3u_text, sanity_check


def _channel(source_id: str, url: str, tvg_id: str = "ESPN.us") -> Channel:
    return Channel(name="ESPN", url=url, source_id=source_id, tvg_id=tvg_id, country="US", language="en")


def test_m3u_text_prefixes_grey_group():
    channel = _channel("grey-sports", "https://example.test/1.m3u8")
    channel.attrs["owl-group-prefix"] = "Grey"
    text = m3u_text([channel], "https://example.test/epg.xml", {})
    assert 'group-title="Grey / Sports"' in text


def _stage(tmp_path, licensed: int, grey: int):
    lines = ["#EXTM3U"]
    for index in range(licensed):
        lines += [f'#EXTINF:-1 group-title="News",N{index}', f"https://example.test/l{index}.m3u8"]
    for index in range(grey):
        lines += [f'#EXTINF:-1 group-title="Grey / Sports",G{index}', f"https://example.test/g{index}.m3u8"]
    (tmp_path / "playlist.m3u").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (tmp_path / "epg.xml").write_text("<tv></tv>", encoding="utf-8")
    (tmp_path / "stats.json").write_text(json.dumps({"published_streams": licensed + grey}), encoding="utf-8")
    (tmp_path / "sports-guide.json").write_text(
        json.dumps({"events": [], "source_status": {}, "generated_at": "2026-01-01T00:00:00Z"}), encoding="utf-8"
    )
    return tmp_path


def test_sanity_floor_ignores_grey(tmp_path):
    sanity_check(_stage(tmp_path, licensed=2, grey=50), 2)


def test_sanity_floor_fails_without_licensed(tmp_path):
    with pytest.raises(ValueError, match="licensed"):
        sanity_check(_stage(tmp_path, licensed=0, grey=50), 2)


def test_preselect_caps_grey_per_identity():
    channels = [
        _channel("iptv-org", "https://example.test/licensed.m3u8"),
        _channel("grey-sports", "https://example.test/g1.m3u8"),
        _channel("grey-sports", "https://example.test/g2.m3u8"),
    ]
    selected, _ = preselect_candidates(channels, 4, 100, {"grey-sports": 1})
    assert sum(c.source_id == "grey-sports" for c in selected) == 1


def test_select_streams_keeps_licensed_primary():
    licensed = _channel("iptv-org", "https://example.test/licensed.m3u8")
    grey = _channel("grey-sports", "https://example.test/g1.m3u8")
    validations = {
        licensed.url: Validation(ok=True, reliability=80.0, height=720),
        grey.url: Validation(ok=True, reliability=0.0, error="validation skipped (source policy)"),
    }
    selected, _ = select_streams([licensed, grey], validations, 1, ["US"], {"grey-sports": 0})
    roles = {channel.source_id: channel.role for channel in selected}
    assert roles["iptv-org"] == "primary"
    assert roles.get("grey-sports") == "backup"
