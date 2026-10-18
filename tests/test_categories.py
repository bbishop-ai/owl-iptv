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


def test_sports_capable_channels_win_over_generic_source_groups():
    assert simple_category(channel("SEC Network", "")) == "Sports"
    assert simple_category(channel("Big Ten Network", "")) == "Sports"
    assert simple_category(channel("PFL MMA", "")) == "Sports"
    assert simple_category(channel("NASCAR Channel", "Lifestyle")) == "Sports"
    assert simple_category(channel("Outdoor Channel", "Lifestyle")) == "Sports"
    assert simple_category(channel("ESPNews", "News;Sports")) == "Sports"
    assert simple_category(channel("CNN", "News;General")) == "News"
    assert simple_category(channel("USA Network", "Comedy;Movies;Series")) == "Movies"
    assert simple_category(channel("TBS", "Entertainment")) == "Entertainment"


def test_word_boundary_safety_no_false_sports():
    """Tokens use word boundaries: TF1/SF1 are not Formula 1; unboxing is not boxing."""
    assert simple_category(channel("TF1", "Entertainment")) == "Entertainment"
    assert simple_category(channel("SF1", "Entertainment")) == "Entertainment"
    assert simple_category(channel("Unboxing Central", "Entertainment")) == "Entertainment"


def test_published_m3u_uses_one_simple_group_title():
    c = channel("Comedy Central", "Comedy;Series;Entertainment")
    output = m3u_text([c], "https://example.test/epg.xml", {c.url: Validation(ok=True)})
    assert 'group-title="Entertainment"' in output
    assert ';Series;Entertainment' not in output
