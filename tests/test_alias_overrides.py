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


def test_shipped_config_carries_six_affiliate_entries():
    from iptv_aggregator.config import load_config
    cfg = load_config(r"config/pipeline.yml")
    overrides = cfg["epg"]["alias_overrides"]
    assert len(overrides) == 6
    assert {entry["tvg_name"].split()[0] for entry in overrides} == {"CBS", "Fox", "NBC"}
