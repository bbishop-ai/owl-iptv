from iptv_aggregator.metadata import MetadataCatalog
from iptv_aggregator.models import Channel


def test_authoritative_metadata_enriches_language_country_and_id():
    catalog = MetadataCatalog([{"id": "CNN.us", "name": "CNN", "alt_names": [], "country": "US", "languages": ["eng"]}])
    channel = Channel("CNN", "https://example.test/live", "sample")
    assert catalog.enrich(channel) == "normalized-name"
    assert channel.tvg_id == "CNN.us"
    assert channel.country == "US"
    assert channel.language == "en"


def test_feed_suffix_uses_authoritative_base_channel():
    catalog = MetadataCatalog([{"id": "Narutoenespanol.us", "name": "Naruto en Español", "alt_names": [], "country": "US", "languages": ["spa"]}])
    channel = Channel("Naruto", "https://example.test/live", "sample", tvg_id="Narutoenespanol.us@SD")
    assert catalog.enrich(channel) == "tvg-id"
    assert channel.tvg_id == "Narutoenespanol.us"
    assert channel.language == "es"
