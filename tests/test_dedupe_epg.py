import xml.etree.ElementTree as ET
import json
from pathlib import Path

from iptv_aggregator.dedupe import canonical_url, preselect_candidates, select_streams
from iptv_aggregator.epg import EPGData, match_channels, normalized_epg_id
from iptv_aggregator.models import Channel, Validation
from iptv_aggregator.normalize import normalize_channel
from iptv_aggregator.normalize import simple_category


def test_rank_primary_and_backup_and_strip_volatile_tokens():
    a = normalize_channel(Channel("Test HD", "https://a.test/live?token=one", "a"))
    b = normalize_channel(Channel("Test", "https://b.test/live", "b"))
    vals = {a.url: Validation(ok=True, height=720, latency_ms=20), b.url: Validation(ok=True, height=1080, latency_ms=40)}
    selected, stats = select_streams([a, b], vals, backups=1)
    assert [c.role for c in selected] == ["primary", "backup"]
    assert selected[0].url == b.url
    assert canonical_url("https://a.test/live?token=one") == "https://a.test/live"
    assert stats["identity_duplicates_removed"] == 0


def test_preselection_bounds_work_and_removes_token_variants():
    rows = [normalize_channel(Channel("Test", f"https://a.test/live?token={n}", str(n))) for n in range(8)]
    selected, removed = preselect_candidates(rows, per_identity=4, total_limit=10)
    assert len(selected) == 1
    assert removed == 7


def test_preselection_source_limit_preserves_aria_candidates_before_validation():
    rows = [normalize_channel(Channel("Same channel", f"https://aria.test/{n}", "5dab59855cb7", tvg_id="same")) for n in range(6)]
    selected, removed = preselect_candidates(
        rows,
        per_identity=4,
        total_limit=20,
        per_source={"5dab59855cb7": 39},
    )
    assert len(selected) == 6
    assert removed == 0


def test_source_backup_limit_preserves_all_viable_aria_entries():
    rows = [normalize_channel(Channel("Same channel", f"https://aria.test/{n}", "5dab59855cb7", tvg_id="same")) for n in range(6)]
    validations = {row.url: Validation(ok=True) for row in rows}
    selected, stats = select_streams(
        rows,
        validations,
        backups=1,
        backups_by_source={"5dab59855cb7": 39},
    )
    assert len(selected) == 6
    assert stats["identity_duplicates_removed"] == 0


def test_source_backup_limit_does_not_change_other_sources():
    rows = [normalize_channel(Channel("Same channel", f"https://other.test/{n}", "other", tvg_id="same")) for n in range(3)]
    selected, stats = select_streams(rows, {row.url: Validation(ok=True) for row in rows}, backups=1, backups_by_source={"5dab59855cb7": 39})
    assert len(selected) == 2
    assert stats["identity_duplicates_removed"] == 1


def test_epg_exact_id_then_exact_name():
    one = ET.fromstring('<channel id="bbc.one.uk"><display-name>BBC One</display-name></channel>')
    epg = EPGData(channels={"bbc.one.uk": one})
    channels = [normalize_channel(Channel("BBC One HD", "https://x.test", "x"))]
    stats = match_channels(channels, epg)
    assert channels[0].tvg_id == "bbc.one.uk"
    assert stats["epg_exact_name"] == 1


def test_epg_uses_authoritative_aliases():
    station = ET.fromstring('<channel id="WABC.us"><display-name>ABC 7 New York</display-name></channel>')
    epg = EPGData(channels={"WABC.us": station})
    channel = normalize_channel(Channel("WABC-TV", "https://x.test", "x", attrs={"metadata-alt-names": json.dumps(["ABC 7 New York"])}))
    stats = match_channels([channel], epg)
    assert channel.tvg_id == "WABC.us"
    assert stats["epg_exact_name"] == 1


def test_epg_normalizes_provider_ids_without_guessing():
    assert normalized_epg_id("Comet.us2") == "comet.us"
    assert normalized_epg_id("K29ES-D.us_locals1") == "k29es.us"
    station = ET.fromstring('<channel id="Comet.us2"><display-name>Comet</display-name></channel>')
    channel = Channel("Not a name match", "https://x.test", "x", tvg_id="Comet.us")
    stats = match_channels([channel], EPGData(channels={"Comet.us2": station}))
    assert channel.tvg_id == "Comet.us2"
    assert stats["epg_normalized_id"] == 1


def test_epg_normalized_id_requires_a_unique_target():
    east = ET.fromstring('<channel id="Comet.us2"><display-name>Comet East</display-name></channel>')
    west = ET.fromstring('<channel id="Comet.us3"><display-name>Comet West</display-name></channel>')
    channel = Channel("Unrelated", "https://x.test", "x", tvg_id="Comet.us")
    stats = match_channels([channel], EPGData(channels={"Comet.us2": east, "Comet.us3": west}))
    assert channel.tvg_id == "Comet.us"
    assert stats["epg_normalized_id"] == 0
    assert stats["epg_unmatched"] == 1


def test_epg_quality_tokens_do_not_block_name_join():
    station = ET.fromstring('<channel id="AnimeXHIDIVE.us"><display-name>ANIME x HIDIVE</display-name></channel>')
    epg = EPGData(channels={"AnimeXHIDIVE.us": station})
    channel = Channel("ANIME x HIDIVE (720p) [Not 24/7]", "https://x.test", "x")
    stats = match_channels([channel], epg)
    assert channel.tvg_id == "AnimeXHIDIVE.us"
    assert stats["epg_exact_name"] == 1


def test_epg_backup_suffix_does_not_block_name_join():
    station = ET.fromstring('<channel id="SouthPark.us"><display-name>South Park</display-name></channel>')
    epg = EPGData(channels={"SouthPark.us": station})
    channel = Channel("South Park [backup]", "https://x.test", "x")
    stats = match_channels([channel], epg)
    assert channel.tvg_id == "SouthPark.us"
    assert stats["epg_exact_name"] == 1


def test_epg_resolution_variants_not_guessed():
    east = ET.fromstring('<channel id="Feed.us720"><display-name>Feed (720p)</display-name></channel>')
    best = ET.fromstring('<channel id="Feed.us1080"><display-name>Feed (1080p)</display-name></channel>')
    epg = EPGData(channels={"Feed.us720": east, "Feed.us1080": best})
    channel = Channel("Feed (1080p)", "https://x.test", "x", tvg_id="Feed.us")
    stats = match_channels([channel], epg)
    # Both guide names collapse to the same key ("feed"), so the id cannot be
    # picked from names; the original tvg-id stays untouched.
    assert channel.tvg_id == "Feed.us"
    assert stats["epg_unmatched"] == 1


def test_epg_fuzzy_word_order_join():
    station = ET.fromstring('<channel id="TYT.us"><display-name>The Young Turks (TYT)</display-name></channel>')
    epg = EPGData(channels={"TYT.us": station})
    channel = Channel("TYT - The Young Turks", "https://x.test", "x")
    stats = match_channels([channel], epg, fuzzy_threshold=93)
    assert channel.tvg_id == "TYT.us"
    assert stats["epg_fuzzy"] == 1


def test_epg_ambiguous_stripped_key_not_auto_joined():
    sd = ET.fromstring('<channel id="FeedA.us"><display-name>Feed</display-name></channel>')
    hd = ET.fromstring('<channel id="FeedB.us"><display-name>Feed</display-name></channel>')
    epg = EPGData(channels={"FeedA.us": sd, "FeedB.us": hd})
    channel = Channel("Feed [backup]", "https://x.test", "x")
    stats = match_channels([channel], epg)
    # "feed" is claimed by two guide channels, so the join must stay off.
    assert channel.tvg_id == ""
    assert stats["epg_unmatched"] == 1


def test_epg_region_guard_blocks_cross_country_join():
    au = ET.fromstring('<channel id="Oxygen.au"><display-name>Oxygen</display-name></channel>')
    epg = EPGData(channels={"Oxygen.au": au})
    channel = Channel("Oxygen (720p)", "https://x.test", "x", tvg_id="Oxygen.us")
    stats = match_channels([channel], epg)
    # .us channel must not join an .au guide entry with the same name.
    assert channel.tvg_id == "Oxygen.us"
    assert stats["epg_unmatched"] == 1


def test_epg_region_guard_allows_same_region_join():
    us = ET.fromstring('<channel id="Business.Rockstars.distro"><display-name>Business Rockstars</display-name></channel>')
    epg = EPGData(channels={"Business.Rockstars.distro": us})
    channel = Channel("Business Rockstars (720p)", "https://x.test", "x", tvg_id="BusinessRockstars.us")
    stats = match_channels([channel], epg)
    assert channel.tvg_id == "Business.Rockstars.distro"
    assert stats["epg_exact_name"] == 1


def test_epg_no_region_means_no_guard():
    ae = ET.fromstring('<channel id="AWE.ae"><display-name>AWE</display-name></channel>')
    epg = EPGData(channels={"AWE.ae": ae})
    channel = Channel("AWE", "https://x.test", "x", tvg_id="")
    stats = match_channels([channel], epg)
    assert channel.tvg_id == "AWE.ae"
    assert stats["epg_exact_name"] == 1


def test_epg_url_id_join():
    guide = ET.fromstring('<channel id="0b73ace69ebb45eaa249bb87837cb958"><display-name>Red Bull TV US</display-name></channel>')
    epg = EPGData(
        channels={"0b73ace69ebb45eaa249bb87837cb958": guide},
        channel_source={"0b73ace69ebb45eaa249bb87837cb958": "s1"},
    )
    channel = Channel("Red Bull TV US (1080p)", "https://jmp2.uk/plu-0b73ace69ebb45eaa249bb87837cb958", "x", tvg_id="RedBullTV.at@US")
    stats = match_channels([channel], epg)
    assert channel.tvg_id == "0b73ace69ebb45eaa249bb87837cb958"
    assert stats["epg_url_id"] == 1


def test_soft_validation_removes_provably_dead_and_keeps_blocked():
    from iptv_aggregator.validator import Validator

    v = Validator(Path("nope.json"), timeout=5, concurrency=1, ttl_hours=24, deep_limit=0)
    # 404 proves dead -> removed
    assert v._soft_probe_confirms_dead(Validation(ok=False, error="HTTP 404")) is True
    assert v._soft_probe_confirms_dead(Validation(ok=False, error="HTTP 410 Gone")) is True
    assert v._soft_probe_confirms_dead(Validation(ok=False, error="[Errno -2] Name or service not known")) is True
    assert v._soft_probe_confirms_dead(Validation(ok=False, error="Connection refused by peer")) is True
    # 403 block / timeout / TLS quirk do NOT prove dead -> kept
    assert v._soft_probe_confirms_dead(Validation(ok=False, error="HTTP 403")) is False
    assert v._soft_probe_confirms_dead(Validation(ok=False, error="TimeoutError()")) is False
    # Expired/mismatched TLS on a restream host is a probe quirk, not death.
    assert v._soft_probe_confirms_dead(Validation(ok=False, error="SSL: CERTIFICATE_VERIFY_FAILED")) is False
