from pathlib import Path

import pytest

from iptv_aggregator.publish import sanity_check


def test_bad_candidate_cannot_replace_last_known_good(tmp_path: Path):
    production = tmp_path / "public"
    production.mkdir()
    production.joinpath("playlist.m3u").write_text("known-good", encoding="utf-8")
    candidate = tmp_path / "stage"
    candidate.mkdir()
    candidate.joinpath("playlist.m3u").write_text("#EXTM3U\n", encoding="utf-8")
    candidate.joinpath("epg.xml").write_text("<tv/>", encoding="utf-8")
    candidate.joinpath("stats.json").write_text('{"published_streams": 0}', encoding="utf-8")
    with pytest.raises(ValueError):
        sanity_check(candidate, minimum_channels=500)
    assert production.joinpath("playlist.m3u").read_text(encoding="utf-8") == "known-good"
