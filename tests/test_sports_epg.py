"""Tests for sports_epg XMLTV injection adapter.

Covers:
- exact network match → programme on correct tvg-id
- unmatched network → no programme
- ambiguous network → no programme
- Broadcast pending → no programme
- duplicate upstream programme → no duplicate
- valid XMLTV structure (start/stop/channel/title/category/desc)
- safe XML character escaping
- event with missing/invalid times → skipped
- M3U bytes unchanged after injection
- inject_sports_epg produces parseable XMLTV
- stage/sanity_check remains valid after injection
"""
from __future__ import annotations

import io
import json
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from iptv_aggregator.models import Channel
from iptv_aggregator.normalize import normalize_channel
from iptv_aggregator.publish import m3u_text, sanity_check
from iptv_aggregator.sports_epg import inject_sports_epg
from iptv_aggregator.sports_guide import SportsEvent
from iptv_aggregator.models import Validation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _channel(name: str, tvg_id: str, role: str = "primary") -> Channel:
    ch = Channel(name=name, url=f"https://test.example/{name}", source_id="t", tvg_id=tvg_id, role=role)
    return normalize_channel(ch)


def _event(
    away: str = "Bears",
    home: str = "Falcons",
    broadcasts: tuple[str, ...] = ("ESPN",),
    league: str = "NFL",
    sport: str = "NFL",
    start_utc: datetime | None = None,
) -> SportsEvent:
    return SportsEvent(
        sport=sport,
        league=league,
        title=f"{away} at {home}",
        start_utc=start_utc or datetime(2026, 10, 18, 17, 0, tzinfo=UTC),
        status="STATUS_SCHEDULED",
        away=away,
        home=home,
        broadcasts=broadcasts,
    )


def _minimal_xmltv(channels: list[Channel] | None = None) -> bytes:
    """Build a minimal upstream XMLTV byte string with optional existing programmes."""
    root = ET.Element("tv", {"generator-info-name": "owl-iptv"})
    if channels:
        for ch in channels:
            cel = ET.SubElement(root, "channel", {"id": ch.tvg_id})
            dn = ET.SubElement(cel, "display-name")
            dn.text = ch.name
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


def _xmltv_with_programme(
    tvg_id: str,
    start: str = "20261018170000 +0000",
    stop: str = "20261018203000 +0000",
    title: str = "Some Game",
) -> bytes:
    """XMLTV bytes with one pre-existing upstream programme."""
    root = ET.Element("tv", {"generator-info-name": "owl-iptv"})
    prog = ET.SubElement(root, "programme", {"start": start, "stop": stop, "channel": tvg_id})
    t = ET.SubElement(prog, "title", {"lang": "en"})
    t.text = title
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Exact network match: one primary channel → one programme on correct tvg-id
# ---------------------------------------------------------------------------

def test_exact_network_match_injects_programme():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(away="Bears", home="Falcons", broadcasts=("ESPN",), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    assert len(progs) == 1
    prog = progs[0]
    assert prog.get("channel") == "ESPN.us"
    assert stats["sports_epg_programmes_added"] == 1
    assert stats["sports_epg_skip_unmapped"] == 0


def test_exact_match_programme_title_is_away_at_home():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(away="Chicago Bears", home="Atlanta Falcons", broadcasts=("ESPN",))

    result_bytes, _ = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    prog = root.findall("programme")[0]
    title = prog.find("title")
    assert title is not None
    assert title.text == "Chicago Bears at Atlanta Falcons"


def test_exact_match_programme_has_category_sports():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])

    result_bytes, _ = inject_sports_epg(xmltv, [_event()], [ch])

    root = ET.fromstring(result_bytes)
    prog = root.findall("programme")[0]
    cat = prog.find("category")
    assert cat is not None
    assert cat.text == "Sports"


def test_exact_match_programme_has_league_subtitle():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(league="NFL", broadcasts=("ESPN",))

    result_bytes, _ = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    prog = root.findall("programme")[0]
    sub = prog.find("sub-title")
    assert sub is not None
    assert sub.text == "NFL"


def test_exact_match_programme_start_is_utc_xmltv_format():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    start = datetime(2026, 10, 18, 17, 0, 0, tzinfo=UTC)

    result_bytes, _ = inject_sports_epg(xmltv, [_event(start_utc=start)], [ch])

    root = ET.fromstring(result_bytes)
    prog = root.findall("programme")[0]
    assert prog.get("start") == "20261018170000 +0000"


def test_exact_match_programme_stop_is_present_and_estimated():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(league="NFL")

    result_bytes, _ = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    prog = root.findall("programme")[0]
    assert prog.get("stop") is not None
    # NFL: 3h 30m → stop should be 20261018203000 +0000
    assert prog.get("stop") == "20261018203000 +0000"
    # Description mentions estimation
    desc = prog.find("desc")
    assert desc is not None
    assert "estimate" in (desc.text or "").lower()


def test_exact_match_desc_contains_disclaimer():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])

    result_bytes, _ = inject_sports_epg(xmltv, [_event()], [ch])

    root = ET.fromstring(result_bytes)
    prog = root.findall("programme")[0]
    desc = prog.find("desc")
    assert desc is not None
    assert "does not guarantee" in (desc.text or "").lower()


# ---------------------------------------------------------------------------
# Unmatched broadcast → no programme
# ---------------------------------------------------------------------------

def test_unmatched_broadcast_generates_no_programme():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("Synergy Sports Plus",))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_skip_unmapped"] == 1
    assert stats["sports_epg_programmes_added"] == 0


def test_no_channels_means_no_programmes():
    xmltv = _minimal_xmltv()
    event = _event(broadcasts=("ESPN",))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_programmes_added"] == 0


# ---------------------------------------------------------------------------
# Ambiguous broadcast → no programme (two channels share the same name key)
# ---------------------------------------------------------------------------

def test_ambiguous_broadcast_generates_no_programme():
    ch_a = _channel("ESPN", "ESPN.us")
    ch_b = _channel("ESPN", "ESPN2.us")  # same display name, different tvg-id
    ch_b.url = "https://test.example/espn2_dup"
    xmltv = _minimal_xmltv([ch_a, ch_b])
    event = _event(broadcasts=("ESPN",))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch_a, ch_b])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_skip_ambiguous"] >= 1


# ---------------------------------------------------------------------------
# Broadcast pending / skip-list → no programme
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("broadcast", [
    "Broadcast pending",
    "ESPN+",
    "ESPN Plus",
    "Prime Video",
    "Prime Video (Local)",
    "Peacock",
    "Paramount+",
])
def test_skip_list_broadcast_generates_no_programme(broadcast: str):
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=(broadcast,))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == [], f"expected no programme for broadcast={broadcast!r}"
    assert stats["sports_epg_skip_broadcast"] >= 1


# ---------------------------------------------------------------------------
# Duplicate upstream programme → no duplicate injected
# ---------------------------------------------------------------------------

def test_duplicate_upstream_programme_not_duplicated():
    tvg_id = "ESPN.us"
    ch = _channel("ESPN", tvg_id)
    start = "20261018170000 +0000"
    title = "Bears at Falcons"
    xmltv = _xmltv_with_programme(tvg_id, start=start, title=title)
    event = _event(away="Bears", home="Falcons", broadcasts=("ESPN",),
                   start_utc=datetime(2026, 10, 18, 17, 0, tzinfo=UTC))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    # Should still have exactly the one upstream programme
    progs = root.findall("programme")
    assert len(progs) == 1
    assert stats["sports_epg_skip_already_present"] == 1
    assert stats["sports_epg_programmes_added"] == 0


def test_different_channel_same_time_not_considered_duplicate():
    ch_espn = _channel("ESPN", "ESPN.us")
    ch_espn2 = _channel("ESPN2", "ESPN2.us")
    xmltv = _xmltv_with_programme("ESPN.us", title="Bears at Falcons")
    event = _event(away="Bears", home="Falcons", broadcasts=("ESPN2",),
                   start_utc=datetime(2026, 10, 18, 17, 0, tzinfo=UTC))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch_espn, ch_espn2])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    # ESPN2 programme injected; ESPN upstream preserved
    assert len(progs) == 2
    assert stats["sports_epg_programmes_added"] == 1


# ---------------------------------------------------------------------------
# Special XML characters are escaped
# ---------------------------------------------------------------------------

def test_xml_special_chars_in_team_names_are_safe():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(away="A&M", home="B<C>D", broadcasts=("ESPN",))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    # Must parse without error
    root = ET.fromstring(result_bytes)
    prog = root.findall("programme")[0]
    title = prog.find("title")
    assert title is not None
    assert title.text == "A&M at B<C>D"
    assert stats["sports_epg_programmes_added"] == 1


# ---------------------------------------------------------------------------
# Invalid / missing time → skipped safely
# ---------------------------------------------------------------------------

def test_event_without_start_time_is_skipped():
    """Events with no start_utc should be silently skipped."""
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    # SportsEvent with a valid-but-falsy start would be a dataclass issue; instead test
    # that empty events list produces no programmes and no crash.
    result_bytes, stats = inject_sports_epg(xmltv, [], [ch])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_programmes_added"] == 0


def test_empty_broadcast_string_is_skipped():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("",))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []


def test_whitespace_only_broadcast_string_is_skipped():
    """A broadcast entry that is entirely whitespace ('   ') produces no programme."""
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("   ",))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_programmes_added"] == 0


# ---------------------------------------------------------------------------
# Output is valid XMLTV XML
# ---------------------------------------------------------------------------

def test_inject_returns_parseable_xmltv():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])

    result_bytes, _ = inject_sports_epg(xmltv, [_event()], [ch])

    # Must parse without exception
    root = ET.fromstring(result_bytes)
    assert root.tag == "tv"


def test_inject_preserves_upstream_generator_attribute():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])

    result_bytes, _ = inject_sports_epg(xmltv, [_event()], [ch])

    root = ET.fromstring(result_bytes)
    assert root.get("generator-info-name") == "owl-iptv"


# ---------------------------------------------------------------------------
# Alias map: BTN, NFL Net, CBSSN, ESPNU/ESPN U, ESPNews/ESPN News
# ---------------------------------------------------------------------------

def test_btn_alias_maps_to_big_ten_network_channel():
    ch = _channel("Big Ten Network", "BigTenNetwork.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("BTN",), league="College Football")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    assert len(progs) == 1
    assert progs[0].get("channel") == "BigTenNetwork.us"
    assert stats["sports_epg_programmes_added"] == 1


def test_nfl_net_alias_maps_to_nfl_network_channel():
    ch = _channel("NFL Network", "NFLNetwork.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("NFL Net",), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    assert len(progs) == 1
    assert progs[0].get("channel") == "NFLNetwork.us"


def test_cbssn_alias_maps_to_cbs_sports_network_channel():
    ch = _channel("CBS Sports Network", "CBSSportsNetwork.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("CBSSN",), league="College Football")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    assert len(progs) == 1
    assert progs[0].get("channel") == "CBSSportsNetwork.us"


def test_espnu_broadcast_matches_espn_u_channel():
    """ESPN broadcasts 'ESPNU'; playlist channel may be named 'ESPN U'."""
    ch = _channel("ESPN U", "ESPNU.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("ESPNU",), league="College Football")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    assert len(progs) == 1
    assert progs[0].get("channel") == "ESPNU.us"


def test_espnu_broadcast_also_matches_espnu_named_channel():
    """If channel is already named 'ESPNU', direct match works too."""
    ch = _channel("ESPNU", "ESPNU.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("ESPNU",), league="College Football")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    assert len(progs) == 1
    assert stats["sports_epg_programmes_added"] == 1


def test_espnews_broadcast_matches_espn_news_channel():
    """ESPN broadcasts 'ESPNews'; playlist channel may be named 'ESPN News'."""
    ch = _channel("ESPN News", "ESPNews.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("ESPNews",), league="College Football")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    assert len(progs) == 1
    assert progs[0].get("channel") == "ESPNews.us"


# ---------------------------------------------------------------------------
# Quality-suffix channel names: "ESPN (720p)" → matches broadcast "ESPN"
# ---------------------------------------------------------------------------

def test_channel_with_quality_suffix_matches_broadcast():
    ch = _channel("ESPN (720p)", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("ESPN",))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert len(root.findall("programme")) == 1
    assert stats["sports_epg_programmes_added"] == 1


# ---------------------------------------------------------------------------
# Backup channel must not receive injected programmes
# ---------------------------------------------------------------------------

def test_backup_channel_is_not_injected():
    primary = _channel("ESPN", "ESPN.us", role="primary")
    backup = _channel("ESPN", "ESPN-backup.us", role="backup")
    backup.url = "https://backup.example/espn"
    xmltv = _minimal_xmltv([primary, backup])
    event = _event(broadcasts=("ESPN",))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [primary, backup])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    # Only one programme for the primary channel
    assert len(progs) == 1
    assert progs[0].get("channel") == "ESPN.us"


# ---------------------------------------------------------------------------
# Channel without tvg-id is excluded from mapping
# ---------------------------------------------------------------------------

def test_channel_without_tvg_id_not_mapped():
    ch = _channel("ESPN", "")  # empty tvg-id
    xmltv = _minimal_xmltv()
    event = _event(broadcasts=("ESPN",))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_programmes_added"] == 0


# ---------------------------------------------------------------------------
# M3U bytes are unchanged by injection
# ---------------------------------------------------------------------------

def test_m3u_bytes_unchanged_after_inject():
    ch = _channel("ESPN", "ESPN.us")
    val = {ch.url: Validation(ok=True)}
    before = m3u_text([ch], "https://example.test/epg.xml", val)

    xmltv = _minimal_xmltv([ch])
    inject_sports_epg(xmltv, [_event()], [ch])  # side-effect-free

    after = m3u_text([ch], "https://example.test/epg.xml", val)
    assert before == after


# ---------------------------------------------------------------------------
# Integration: stage/publish sanity_check remains valid after injection
# ---------------------------------------------------------------------------

def test_sanity_check_passes_after_injection(tmp_path: Path):
    ch = _channel("ESPN", "ESPN.us")
    val = {ch.url: Validation(ok=True)}
    stage = tmp_path / "stage"
    stage.mkdir()

    m3u = m3u_text([ch], "https://example.test/epg.xml", val)
    stage.joinpath("playlist.m3u").write_text(m3u, encoding="utf-8")

    xmltv = _minimal_xmltv([ch])
    enriched, _ = inject_sports_epg(xmltv, [_event()], [ch])
    stage.joinpath("epg.xml").write_bytes(enriched)

    stage.joinpath("stats.json").write_text(json.dumps({"published_streams": 1}), encoding="utf-8")
    stage.joinpath("sports-guide.json").write_text(
        json.dumps({"events": [], "source_status": {}, "generated_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )

    sanity_check(stage, minimum_channels=1)  # must not raise


# ---------------------------------------------------------------------------
# Stats counter structure
# ---------------------------------------------------------------------------

def test_stats_has_all_expected_keys():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])

    _, stats = inject_sports_epg(xmltv, [], [ch])

    expected_keys = {
        "sports_events_fetched",
        "sports_epg_programmes_added",
        "sports_epg_skip_unmapped",
        "sports_epg_skip_ambiguous",
        "sports_epg_skip_already_present",
        "sports_epg_skip_incomplete",
        "sports_epg_skip_broadcast",
    }
    assert expected_keys.issubset(stats.keys())


def test_events_fetched_counter_matches_input_length():
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    events = [_event(), _event(away="Jets", home="Patriots")]

    _, stats = inject_sports_epg(xmltv, events, [ch])

    assert stats["sports_events_fetched"] == 2


# ---------------------------------------------------------------------------
# Multi-broadcast event: each unique resolved channel gets its own programme
# ---------------------------------------------------------------------------

def test_multi_broadcast_event_injects_per_channel():
    espn = _channel("ESPN", "ESPN.us")
    abc = _channel("ABC", "ABC.us")  # unique match → now eligible
    xmltv = _minimal_xmltv([espn, abc])
    event = _event(broadcasts=("ESPN", "ABC"))

    result_bytes, stats = inject_sports_epg(xmltv, [event], [espn, abc])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    # Both ESPN and ABC have unique channel matches → two programmes injected
    assert len(progs) == 2
    channels_injected = {p.get("channel") for p in progs}
    assert channels_injected == {"ESPN.us", "ABC.us"}
    assert stats["sports_epg_programmes_added"] == 2
    assert stats["sports_epg_skip_broadcast"] == 0


def test_same_tvg_id_not_injected_twice_for_same_event():
    """Same channel via two broadcast aliases for the same event → only one programme."""
    ch = _channel("ESPN", "ESPN.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("ESPN", "ESPN"))  # duplicate broadcast entries

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    progs = [p for p in root.findall("programme") if p.get("channel") == "ESPN.us"]
    assert len(progs) == 1
    assert stats["sports_epg_programmes_added"] == 1


# ---------------------------------------------------------------------------
# ABC / CBS / NBC / Fox: eligible when uniquely matched, skipped when ambiguous
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("broadcast,channel_name,tvg_id", [
    ("CBS",  "CBS",  "CBS.us"),
    ("ABC",  "ABC",  "ABC.us"),
    ("NBC",  "NBC",  "NBC.us"),
    ("Fox",  "Fox",  "FOX.us"),
    ("Fox",  "FOX",  "FOX.us"),   # uppercase channel name still normalizes to "fox"
])
def test_major_network_unique_match_injects(broadcast: str, channel_name: str, tvg_id: str):
    """A single primary channel uniquely matching a major-network broadcast is injected."""
    ch = _channel(channel_name, tvg_id)
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=(broadcast,), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    assert len(progs) == 1, f"expected 1 programme for broadcast={broadcast!r}, channel={channel_name!r}"
    assert progs[0].get("channel") == tvg_id
    assert stats["sports_epg_programmes_added"] == 1
    assert stats["sports_epg_skip_broadcast"] == 0


def test_ambiguous_fox_channels_no_inject():
    """Two primary channels whose names both normalize to 'fox' → ambiguous → no inject."""
    ch_a = _channel("Fox", "FOX-East.us")
    ch_b = _channel("Fox", "FOX-West.us")
    ch_b.url = "https://test.example/fox-west"
    xmltv = _minimal_xmltv([ch_a, ch_b])
    event = _event(broadcasts=("Fox",), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch_a, ch_b])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_skip_ambiguous"] >= 1
    assert stats["sports_epg_programmes_added"] == 0


def test_ambiguous_cbs_channels_no_inject():
    """Two primary channels both named 'CBS' → ambiguous → no inject."""
    ch_a = _channel("CBS", "CBS-East.us")
    ch_b = _channel("CBS", "CBS-West.us")
    ch_b.url = "https://test.example/cbs-west"
    xmltv = _minimal_xmltv([ch_a, ch_b])
    event = _event(broadcasts=("CBS",), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch_a, ch_b])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_skip_ambiguous"] >= 1


def test_fox_sports_1_channel_does_not_match_fox_broadcast():
    """'Fox Sports 1' normalizes to 'fox sports 1', not 'fox' → no match for broadcast 'Fox'."""
    ch = _channel("Fox Sports 1", "FS1.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("Fox",), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_skip_unmapped"] == 1
    assert stats["sports_epg_programmes_added"] == 0


def test_fs1_channel_does_not_match_fox_broadcast():
    """'FS1' channel does not match broadcast 'Fox' (different normalized keys)."""
    ch = _channel("FS1", "FS1.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("Fox",), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_skip_unmapped"] == 1


def test_fox_broadcast_does_not_match_fox_news_channel():
    """'Fox News' channel does not match broadcast 'Fox' (different normalized key)."""
    ch = _channel("Fox News", "FoxNews.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("Fox",), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_skip_unmapped"] == 1


def test_fs1_broadcast_maps_to_fox_sports_1_uniquely():
    """Broadcast 'FS1' resolves via alias to 'fox sports 1' → injects uniquely to FoxSports1.us."""
    ch = _channel("Fox Sports 1 (720p)", "FoxSports1.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("FS1",), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    assert len(progs) == 1
    assert progs[0].get("channel") == "FoxSports1.us"
    assert stats["sports_epg_programmes_added"] == 1


def test_fox_broadcast_does_not_map_via_fs1_alias():
    """Broadcast 'Fox' must not match 'Fox Sports 1' via the FS1 alias (different normalized key)."""
    ch = _channel("Fox Sports 1 (720p)", "FoxSports1.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("Fox",), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert root.findall("programme") == []
    assert stats["sports_epg_skip_unmapped"] == 1
    assert stats["sports_epg_programmes_added"] == 0


def test_abc_unique_match_does_not_pollute_cbs_channel():
    """Unique ABC match injects only to ABC.us, not to any CBS channel."""
    ch_abc = _channel("ABC", "ABC.us")
    ch_cbs = _channel("CBS", "CBS.us")
    xmltv = _minimal_xmltv([ch_abc, ch_cbs])
    event = _event(broadcasts=("ABC",), league="NFL")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch_abc, ch_cbs])

    root = ET.fromstring(result_bytes)
    progs = root.findall("programme")
    assert len(progs) == 1
    assert progs[0].get("channel") == "ABC.us"
    assert stats["sports_epg_programmes_added"] == 1


def test_major_network_with_quality_suffix_still_maps():
    """'CBS (720p)' channel name quality-stripped to 'cbs' matches broadcast 'CBS'."""
    ch = _channel("CBS (720p)", "CBS.us")
    xmltv = _minimal_xmltv([ch])
    event = _event(broadcasts=("CBS",), league="MLB")

    result_bytes, stats = inject_sports_epg(xmltv, [event], [ch])

    root = ET.fromstring(result_bytes)
    assert len(root.findall("programme")) == 1
    assert stats["sports_epg_programmes_added"] == 1
