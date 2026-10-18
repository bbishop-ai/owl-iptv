import pytest

from datetime import UTC, datetime
from pathlib import Path

from iptv_aggregator.sports_guide import (
    _ESPN_MAX_RESPONSE_BYTES,
    _fetch_json,
    event_payload,
    parse_scoreboard,
    playlist_networks,
    render_html,
    write_sports_guide,
)


def _scoreboard(*events: dict) -> dict:
    return {"events": list(events)}


def _event(
    *,
    name: str = "Chicago Bears at Atlanta Falcons",
    date: str = "2026-10-18T17:00Z",
    broadcasts: list[dict] | None = None,
    status: str = "STATUS_IN_PROGRESS",
) -> dict:
    return {
        "name": name,
        "date": date,
        "status": {"type": {"name": status}},
        "competitions": [{
            "competitors": [
                {"homeAway": "away", "team": {"displayName": "Chicago Bears"}},
                {"homeAway": "home", "team": {"displayName": "Atlanta Falcons"}},
            ],
            "broadcasts": broadcasts or [{"market": "national", "names": ["Fox", "FOX"]}],
        }],
    }


def test_parse_scoreboard_keeps_national_broadcast():
    events = parse_scoreboard("nfl", _scoreboard(_event()))

    assert len(events) == 1
    event = events[0]
    assert event.league == "NFL"
    assert event.sport == "NFL"
    assert event.away == "Chicago Bears"
    assert event.home == "Atlanta Falcons"
    assert event.broadcasts == ("Fox",)
    assert event.start_utc == datetime(2026, 10, 18, 17, 0, tzinfo=UTC)


def test_parse_scoreboard_accepts_empty_or_incomplete_events():
    assert parse_scoreboard("college-football", _scoreboard()) == []
    assert parse_scoreboard("nfl", {"events": [{"name": "TBD"}]}) == []


def test_event_payload_sorts_and_reports_exact_playlist_presence():
    events = parse_scoreboard("nfl", _scoreboard(
        _event(name="Late game", date="2026-10-18T22:00Z", broadcasts=[{"names": ["NBC"]}]),
        _event(name="Early game", date="2026-10-18T17:00Z", broadcasts=[{"names": ["Fox"]}]),
    ))

    payload = event_payload(
        events,
        {"nfl": "ok"},
        generated_at=datetime(2026, 10, 18, 16, tzinfo=UTC),
        playlist_names=["FOX", "NBC News NOW", "TBS"],
    )

    assert [event["title"] for event in payload["events"]] == ["Early game", "Late game"]
    assert payload["events"][0]["available_in_playlist"] == ["Fox"]
    assert payload["events"][1]["available_in_playlist"] == []
    assert payload["source_status"] == {"nfl": "ok"}
    assert "site.api.espn.com" in render_html(payload)


def test_write_sports_guide_writes_json_and_html_without_playlist_changes(tmp_path: Path):
    events = parse_scoreboard("nfl", _scoreboard(_event()))
    playlist = tmp_path / "playlist.m3u"
    playlist.write_text("#EXTM3U\n#EXTINF:-1,FOX\nhttps://example.test/fox\n", encoding="utf-8")

    write_sports_guide(
        tmp_path,
        events,
        {"nfl": "ok"},
        generated_at=datetime(2026, 10, 18, 16, tzinfo=UTC),
        playlist_names=["FOX"],
    )

    payload = __import__("json").loads(tmp_path.joinpath("sports-guide.json").read_text(encoding="utf-8"))
    assert payload["events"][0]["available_in_playlist"] == ["Fox"]
    assert "Schedule information only" in tmp_path.joinpath("sports-guide.html").read_text(encoding="utf-8")
    assert playlist.read_text(encoding="utf-8") == "#EXTM3U\n#EXTINF:-1,FOX\nhttps://example.test/fox\n"


def test_fetch_json_rejects_oversized_response():
    """Responses above the cap must raise ValueError, not be parsed."""
    from unittest.mock import MagicMock, patch

    mock_response = MagicMock()
    mock_response.__enter__ = MagicMock(return_value=mock_response)
    mock_response.__exit__ = MagicMock(return_value=False)
    mock_response.read.return_value = b"\x00" * (_ESPN_MAX_RESPONSE_BYTES + 1)

    with patch("urllib.request.urlopen", return_value=mock_response):
        with pytest.raises(ValueError, match="too large"):
            _fetch_json("https://example.test/", timeout_seconds=5)
