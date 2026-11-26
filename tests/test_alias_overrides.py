import xml.etree.ElementTree as ET

from iptv_aggregator.epg import EPGData, match_channels
from iptv_aggregator.models import Channel
from iptv_aggregator.pipeline import apply_epg_alias_overrides

OVERRIDES = [
    {"tvg_name": "CBS KBZK (1080p)", "alt_names": ["KBZK", "KBZK-DT"]},
    {"tvg_name": "NBC KOAA-TV (1080p)", "alt_names": ["KOAA", "KOAA-DT"]},
]


def _row(name: str, tvg_id: str, role: str = "primary") -> Channel:
    channel = Channel(name=f"{name} [suffix noise]" if role == "backup" else name,
                      url=f"https://x.test/{name.replace(' ', '-')}", source_id="owl")
    channel.tvg_id = tvg_id
    channel.tvg_name = name
    channel.role = role
    return channel


def _guide(*entries) -> EPGData:
    """entries: (id, display names, [(start, title)])."""
    epg = EPGData()
    for eid, names, progs in entries:
        element = ET.Element("channel", {"id": eid})
        for value in names:
            sub = ET.SubElement(element, "display-name")
            sub.text = value
        epg.channels[eid] = element
        epg.channel_source[eid] = "locals-guide"
        for start, title in progs:
            programme = ET.Element("programme", {"start": start, "stop": start, "channel": eid})
            title_el = ET.SubElement(programme, "title")
            title_el.text = title
            epg.programmes[eid].append(programme)
    return epg


GUIDE = _guide(
    ("KBZK-DT.us_locals1", ["KBZK-DT"], [("20261126180000 +0000", "Live: NFL Football")]),
    ("KOAA-DT.us_locals1", ["KOAA-DT"], [("20261127011500 +0000", "Live: NFL Football")]),
)


def test_alias_overrides_join_network_rows_to_station_ids():
    channels = [_row("CBS KBZK (1080p)", "CBS.us"), _row("NBC KOAA-TV (1080p)", "NBC.us")]
    stats = apply_epg_alias_overrides(channels, OVERRIDES)
    assert stats == {"alias_override_applied": 2, "alias_override_skipped_ambiguous": 0}
    match_stats = match_channels(channels, GUIDE)
    assert match_stats["epg_exact_name"] == 2
    assert channels[0].tvg_id == "KBZK-DT.us_locals1"
    assert channels[1].tvg_id == "KOAA-DT.us_locals1"
    assert all(c.attrs["epg_confidence"] == "exact_name" for c in channels)
    assert all(c.attrs["owl-alias-source"] == "config" for c in channels)


def test_without_overrides_the_rows_stay_unmatched():
    channels = [_row("CBS KBZK (1080p)", "CBS.us"), _row("NBC KOAA-TV (1080p)", "NBC.us")]
    apply_epg_alias_overrides(channels, [])
    match_channels(channels, GUIDE)
    assert all(c.tvg_id in {"CBS.us", "NBC.us"} for c in channels)
    assert all(c.attrs["epg_match_method"] == "none" for c in channels)


def test_ambiguous_tvg_name_is_skipped_untouched():
    dupes = [_row("CBS KBZK (1080p)", "CBS.us"), _row("CBS KBZK (1080p)", "CBS2.us")]
    stats = apply_epg_alias_overrides(dupes, [{"tvg_name": "CBS KBZK (1080p)", "alt_names": ["KBZK-DT"]}])
    assert stats["alias_override_applied"] == 0
    assert stats["alias_override_skipped_ambiguous"] == 2
    assert all("metadata-alt-names" not in c.attrs for c in dupes)


def test_existing_catalog_alt_names_are_preserved():
    channel = _row("CBS KBZK (1080p)", "CBS.us")
    channel.attrs["metadata-alt-names"] = '["Catalog Name"]'
    apply_epg_alias_overrides([channel], [{"tvg_name": "CBS KBZK (1080p)", "alt_names": ["KBZK-DT"]}])
    values = channel.attrs["metadata-alt-names"]
    assert "Catalog Name" in values and "KBZK-DT" in values


def test_unknown_tvg_name_is_a_harmless_noop():
    stats = apply_epg_alias_overrides([], [{"tvg_name": "Not A Real Row", "alt_names": ["X"]}])
    assert stats == {"alias_override_applied": 0, "alias_override_skipped_ambiguous": 1}


# ---------------------------------------------------------------------------
# Generic affiliate call-sign rule (apply_affiliate_callsign_aliases)
# ---------------------------------------------------------------------------

from iptv_aggregator.pipeline import (  # noqa: E402
    _AFFILIATE_CALLSIGN,
    _AFFILIATE_BRAND_PREFIX,
    _AFFILIATE_SUBBRAND,
    apply_affiliate_callsign_aliases,
)


AFFILIATE_ROWS = [
    "CBS KBZK (1080p) [backup]",
    "CBS KMOV (1080p) [Geo-blocked]",
    "Fox (United States) KBSI (720p) [Not 24/7]",
    "Fox (United States) KCPQ (1080p)",
    "NBC KJRH-TV (1080p) [backup]",
    "NBC KRIS-TV (1080p)",
    "ABC KAAL (1080p) [240p]",
]


def _call_channel(name: str, tvg_id: str = "CBS.us") -> Channel:
    channel = Channel(name=name, url=f"https://x.test/{abs(hash(name))}", source_id="owl")
    channel.tvg_id = tvg_id
    return channel


def test_rule_extracts_call_signs_from_big_four_network_rows():
    channels = [_call_channel(name, f"Net{i}.us") for i, name in enumerate(AFFILIATE_ROWS)]
    stats = apply_affiliate_callsign_aliases(channels)
    assert stats == {"affiliate_callsign_applied": 7, "affiliate_callsign_skipped": 0}
    assert '"KBZK", "KBZK-DT"' in channels[0].attrs["metadata-alt-names"]
    assert '"KBSI", "KBSI-DT"' in channels[2].attrs["metadata-alt-names"]
    assert channels[3].attrs["owl-alias-source"] == "affiliate-callsign"


def test_rule_joins_the_rows_through_the_existing_matcher():
    guide = _guide(
        ("KBZK-DT.us_locals1", ["KBZK-DT"], [("20261126180000 +0000", "Live: NFL Football")]),
        ("KAAL-DT.us_locals1", ["KAAL-DT"], [("20261126190000 +0000", "Local Programming")]),
    )
    channels = [_call_channel("CBS KBZK (1080p) [backup]", "CBS.us"),
                _call_channel("ABC KAAL (1080p) [240p]", "ABC.us")]
    channels[0].tvg_name = ""   # runtime reality: enriched rows lose tvg-name
    channels[1].tvg_name = ""
    apply_affiliate_callsign_aliases(channels)
    stats = match_channels(channels, guide)
    assert stats["epg_exact_name"] == 2
    assert channels[0].tvg_id == "KBZK-DT.us_locals1"
    assert channels[1].tvg_id == "KAAL-DT.us_locals1"


def test_rule_skips_sub_brands_and_non_network_prefixes():
    channels = [
        _call_channel("Fox Weather KTVU (1080p) [backup]", "Fox.us"),   # sub-brand word
        _call_channel("MovieSphere Gold WFXT (1080p)", "WFXT.us"),      # no network prefix
        _call_channel("MNT KCTV (1080p)", "MNT.us"),                    # non big-four brand
    ]
    stats = apply_affiliate_callsign_aliases(channels)
    assert stats["affiliate_callsign_applied"] == 0
    assert all("owl-alias-source" not in c.attrs for c in channels)


def test_rule_requires_us_region_on_the_row():
    channel = _call_channel("Fox KDFW (1080p) [backup]", "Fox.br")
    stats = apply_affiliate_callsign_aliases([channel])
    assert stats == {"affiliate_callsign_applied": 0, "affiliate_callsign_skipped": 1}
    assert "owl-alias-source" not in channel.attrs


def test_call_sign_regex_stays_conservative():
    assert _AFFILIATE_CALLSIGN.search("CBS KBZK (1080p)").group(1) == "KBZK"
    assert _AFFILIATE_CALLSIGN.search("NBC KOAA-TV (1080p)").group(1) == "KOAA"
    assert _AFFILIATE_CALLSIGN.search("Fox (United States) KCPQ-LD (720p)").group(1) == "KCPQ"
    assert not _AFFILIATE_CALLSIGN.search("News Nation Now")      # lowercase words
    assert not _AFFILIATE_CALLSIGN.search("CBS News Texas")
    assert _AFFILIATE_BRAND_PREFIX.match("fox (united states) kbsi (720p)")
    assert _AFFILIATE_SUBBRAND.search("Fox Weather WFLD (1080p)")
