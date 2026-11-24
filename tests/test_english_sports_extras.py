from __future__ import annotations

import json
from pathlib import Path

from iptv_aggregator.models import Channel
from iptv_aggregator.parse import parse_playlist
from iptv_aggregator.pipeline import apply_forced_locale

ROOT = Path(__file__).resolve().parents[1]


def test_force_us_english_overrides_metadata_country_and_language():
    ch = Channel(name="Fox Sports News", url="https://x.test/a.m3u8", source_id="english-sports-extras", tvg_id="FoxSportsNews.au", country="AU", language="unknown")
    other = Channel(name="Other", url="https://x.test/b.m3u8", source_id="iptv-org", tvg_id="Other.au", country="AU", language="unknown")
    apply_forced_locale([ch, other], [{"id": "english-sports-extras", "force_us_english": True}, {"id": "iptv-org"}])
    assert (ch.country, ch.language) == ("US", "en")
    assert (other.country, other.language) == ("AU", "unknown")


def test_english_sports_extras_source_is_wired():
    sources = json.loads((ROOT / "config/sources.json").read_text(encoding="utf-8"))["sources"]
    src = next(s for s in sources if s["id"] == "english-sports-extras")
    assert src["enabled"] and src["force_us_english"] and src.get("role") != "backup"
    channels = parse_playlist((ROOT / src["path"]).read_text(encoding="utf-8"), src["id"])
    assert len(channels) == 14
    assert all(c.url.startswith("http") and c.tvg_id for c in channels)
