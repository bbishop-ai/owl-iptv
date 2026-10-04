from iptv_aggregator.models import Channel
from iptv_aggregator.normalize import simple_category
from iptv_aggregator.publish import m3u_text
from iptv_aggregator.models import Validation


def channel(name: str, group: str) -> Channel:
    return Channel(name=name, url="https://example.test/live.m3u8", source_id="test", group=group)


def test_simple_categories_collapse_provider_labels():
    assert simple_category(channel("CNN", "News;General")) == "News"
    assert simple_category(channel("ESPN", "Entertainment;Sports")) == "Sports"
    assert simple_category(channel("Classic Cinema", "Movies;Series")) == "Movies"
    assert simple_category(channel("Cartoon Network", "Animation;Kids")) == "Kids"
    assert simple_category(channel("Jazz FM", "Music")) == "Music"
    assert simple_category(channel("Bible Channel", "Religious")) == "Religion"
    assert simple_category(channel("NASA TV", "Education")) == "Education"
    assert simple_category(channel("Local 5 News", "Local")) == "News"
    assert simple_category(channel("Travel Food", "Lifestyle;Cooking")) == "Lifestyle"
    assert simple_category(channel("Mystery Channel", "Undefined")) == "Entertainment"
    assert simple_category(channel("Unclassified", "")) == "Other"


def test_published_m3u_uses_one_simple_group_title():
    c = channel("Comedy Central", "Comedy;Series;Entertainment")
    output = m3u_text([c], "https://example.test/epg.xml", {c.url: Validation(ok=True)})
    assert 'group-title="Entertainment"' in output
    assert ';Series;Entertainment' not in output
