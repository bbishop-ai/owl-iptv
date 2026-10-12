from iptv_aggregator.epg import _url_provider_hex
from iptv_aggregator.normalize import rewrite_pluto_url

RELAY = "https://owl-pluto-relay.bradsai818.workers.dev"
PID = "5aea40b35126c2157123aa64"


def test_jmp2_pluto_link_rewrites_to_relay():
    assert rewrite_pluto_url(f"https://jmp2.uk/plu-{PID}.m3u8", RELAY) == f"{RELAY}/plu/{PID}.m3u8"
    assert rewrite_pluto_url(f"http://jmp2.uk/plu-{PID}.m3u8?x=1", RELAY + "/") == f"{RELAY}/plu/{PID}.m3u8"


def test_non_pluto_and_unconfigured_pass_through():
    for url in (f"https://jmp2.uk/sam-{PID}.m3u8", "https://example.com/live.m3u8", ""):
        assert rewrite_pluto_url(url, RELAY) == url
    assert rewrite_pluto_url(f"https://jmp2.uk/plu-{PID}.m3u8", None) == f"https://jmp2.uk/plu-{PID}.m3u8"
    assert rewrite_pluto_url(f"https://jmp2.uk/plu-{PID}.m3u8", "") == f"https://jmp2.uk/plu-{PID}.m3u8"


def test_epg_provider_id_join_survives_the_rewrite():
    # the +511 Pluto url-id EPG joins must keep working on relay URLs
    assert _url_provider_hex(f"https://jmp2.uk/plu-{PID}.m3u8") == ("pluto", PID)
    assert _url_provider_hex(f"{RELAY}/plu/{PID}.m3u8") == ("pluto", PID)
    assert _url_provider_hex("https://example.com/plu/notahexid.m3u8") == (None, None)
