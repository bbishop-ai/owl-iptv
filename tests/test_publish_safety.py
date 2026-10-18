import json
from pathlib import Path

import pytest

from iptv_aggregator.publish import sanity_check


def _minimal_valid_stage(tmp_path: Path) -> Path:
    """Stage that passes all pre-sports-guide sanity checks (1 stream)."""
    stage = tmp_path / "stage"
    stage.mkdir()
    stage.joinpath("playlist.m3u").write_text(
        "#EXTM3U\n#EXTINF:-1,Test\nhttps://x.test/s\n", encoding="utf-8"
    )
    stage.joinpath("epg.xml").write_text("<tv/>", encoding="utf-8")
    stage.joinpath("stats.json").write_text(
        json.dumps({"published_streams": 1}), encoding="utf-8"
    )
    return stage


def test_sanity_check_rejects_missing_sports_guide(tmp_path: Path):
    stage = _minimal_valid_stage(tmp_path)
    with pytest.raises(Exception):  # FileNotFoundError — sports-guide.json absent
        sanity_check(stage, minimum_channels=1)


def test_sanity_check_rejects_invalid_sports_guide_schema(tmp_path: Path):
    stage = _minimal_valid_stage(tmp_path)
    # Missing required "events" key
    stage.joinpath("sports-guide.json").write_text(
        json.dumps({"source_status": {}, "generated_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="sports guide schema invalid"):
        sanity_check(stage, minimum_channels=1)


def test_sanity_check_accepts_empty_events_list(tmp_path: Path):
    """An empty events list is valid (off-day or source outage)."""
    stage = _minimal_valid_stage(tmp_path)
    stage.joinpath("sports-guide.json").write_text(
        json.dumps({
            "events": [],
            "source_status": {"nfl": "failed:OSError"},
            "generated_at": "2026-01-01T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    sanity_check(stage, minimum_channels=1)  # must not raise


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
