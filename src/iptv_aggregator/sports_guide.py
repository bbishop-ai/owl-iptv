from __future__ import annotations

import asyncio
import html
import json
import re
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

LEAGUES = {
    "college-football": ("College Football", "football/college-football"),
    "nfl": ("NFL", "football/nfl"),
    "mlb": ("MLB", "baseball/mlb"),
    "nba": ("NBA", "basketball/nba"),
    "nhl": ("NHL", "hockey/nhl"),
}


@dataclass(frozen=True, slots=True)
class SportsEvent:
    sport: str
    league: str
    title: str
    start_utc: datetime
    status: str
    away: str
    home: str
    broadcasts: tuple[str, ...]


def _as_utc(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except (AttributeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_scoreboard(league_key: str, payload: dict[str, Any]) -> list[SportsEvent]:
    """Normalize one untrusted ESPN scoreboard document without guessing fields."""
    league = LEAGUES.get(league_key)
    if not league or not isinstance(payload, dict):
        return []
    sport, _ = league
    parsed: list[SportsEvent] = []
    for raw in payload.get("events", []):
        if not isinstance(raw, dict):
            continue
        competitions = raw.get("competitions") or []
        competition = competitions[0] if competitions and isinstance(competitions[0], dict) else {}
        teams: dict[str, str] = {}
        for competitor in competition.get("competitors") or []:
            if not isinstance(competitor, dict):
                continue
            side = competitor.get("homeAway")
            team = competitor.get("team") or {}
            name = team.get("displayName") if isinstance(team, dict) else None
            if side in {"away", "home"} and isinstance(name, str) and name.strip():
                teams[side] = name.strip()
        start = _as_utc(raw.get("date", ""))
        title = raw.get("name")
        if not (start and isinstance(title, str) and title.strip() and teams.get("away") and teams.get("home")):
            continue
        broadcasts: list[str] = []
        for broadcast in competition.get("broadcasts") or []:
            if not isinstance(broadcast, dict):
                continue
            for name in broadcast.get("names") or []:
                if isinstance(name, str) and name.strip() and name.strip().casefold() not in {item.casefold() for item in broadcasts}:
                    broadcasts.append(name.strip())
        status = ((raw.get("status") or {}).get("type") or {}).get("name") or "UNKNOWN"
        parsed.append(SportsEvent(sport, sport, title.strip(), start, str(status), teams["away"], teams["home"], tuple(broadcasts)))
    return sorted(parsed, key=lambda event: (event.start_utc, event.league, event.away, event.home))


def _network_key(value: str) -> str | None:
    text = re.sub(r"\s+", " ", value).strip().casefold()
    exact = {"abc", "cbs", "nbc", "fox", "tbs", "tnt", "usa", "usa network", "espn", "espn2", "espnu", "espnews", "fs1", "fs2", "acc network", "big ten network", "btn", "sec network", "cbssn", "cbs sports network"}
    if text in exact:
        return {"usa network": "usa", "btn": "big ten network", "cbssn": "cbs sports network"}.get(text, text)
    # Only recognize local affiliates; exclude brands such as Fox News and CBS News.
    for network in ("abc", "cbs", "nbc", "fox", "tbs", "tnt"):
        if re.match(rf"^{network}(?:\s|\()", text) and not re.match(rf"^{network}\s+(?:news|business|sports)", text):
            return network
    return None


def playlist_networks(names: Iterable[str]) -> set[str]:
    return {key for name in names if (key := _network_key(name))}


def event_payload(events: Iterable[SportsEvent], source_status: dict[str, str], *, generated_at: datetime, playlist_names: Iterable[str]) -> dict[str, Any]:
    networks = playlist_networks(playlist_names)
    ordered = sorted(events, key=lambda event: (event.start_utc, event.league, event.away, event.home))
    return {
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "window_start_utc": generated_at.astimezone(UTC).isoformat(),
        "window_end_utc": (generated_at.astimezone(UTC) + timedelta(hours=72)).isoformat(),
        "source_status": dict(sorted(source_status.items())),
        "events": [
            {
                "sport": event.sport,
                "league": event.league,
                "title": event.title,
                "start_utc": event.start_utc.isoformat(),
                "status": event.status,
                "away": event.away,
                "home": event.home,
                "broadcasts": list(event.broadcasts),
                "available_in_playlist": [name for name in event.broadcasts if _network_key(name) in networks],
            }
            for event in ordered
        ],
    }


_ESPN_MAX_RESPONSE_BYTES = 4 * 1024 * 1024  # 4 MB; typical scoreboard is <200 KB


def _fetch_json(url: str, timeout_seconds: int) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "owl-iptv/1.0"})
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        raw = response.read(_ESPN_MAX_RESPONSE_BYTES + 1)
    if len(raw) > _ESPN_MAX_RESPONSE_BYTES:
        raise ValueError(f"ESPN response too large: >{_ESPN_MAX_RESPONSE_BYTES} bytes")
    return json.loads(raw)


async def fetch_events(now: datetime | None = None, timeout_seconds: int = 15) -> tuple[list[SportsEvent], dict[str, str]]:
    """Fetch a bounded 72-hour event window; failures become visible status, never fake events."""
    now = (now or datetime.now(UTC)).astimezone(UTC)
    dates = {(now + timedelta(days=offset)).strftime("%Y%m%d") for offset in range(4)}
    events: list[SportsEvent] = []
    statuses: dict[str, str] = {}
    for key, (_, path) in LEAGUES.items():
        league_events: list[SportsEvent] = []
        errors: list[str] = []
        for date in sorted(dates):
            url = f"https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={date}&limit=100"
            try:
                payload = await asyncio.to_thread(_fetch_json, url, timeout_seconds)
            except (OSError, TimeoutError, ValueError) as exc:
                errors.append(type(exc).__name__)
                continue
            league_events.extend(parse_scoreboard(key, payload))
        unique = {(event.title, event.start_utc, event.league): event for event in league_events}
        events.extend(unique.values())
        statuses[key] = "ok" if league_events or not errors else f"failed:{errors[0]}"
    return sorted(events, key=lambda event: (event.start_utc, event.league, event.away, event.home)), statuses


def render_html(payload: dict[str, Any]) -> str:
    groups: dict[str, list[dict[str, Any]]] = {label: [] for label, _ in LEAGUES.values()}
    for event in payload.get("events", []):
        groups.setdefault(event.get("sport", "Other"), []).append(event)
    sections: list[str] = []
    for sport, events in groups.items():
        rows = []
        for event in events:
            broadcasts = ", ".join(event.get("broadcasts", [])) or "Broadcast pending"
            availability = ", ".join(event.get("available_in_playlist", [])) or "Schedule only"
            rows.append(
                f"<li><time datetime=\"{html.escape(event['start_utc'])}\">{html.escape(event['start_utc'])}</time> — "
                f"<strong>{html.escape(event['title'])}</strong> · {html.escape(broadcasts)} · {html.escape(availability)}</li>"
            )
        sections.append(f"<section><h2>{html.escape(sport)}</h2><ul>{''.join(rows) or '<li>No scheduled events in the current window.</li>'}</ul></section>")
    status = html.escape(json.dumps(payload.get("source_status", {}), sort_keys=True))
    generated_at = html.escape(payload["generated_at"])
    return (
        "<!doctype html><meta charset=utf-8><title>Owl Game Finder</title><h1>Owl Game Finder</h1>"
        "<p>Schedule information only. A network listed here is not a promise that a stream is playable.</p>"
        "<p>Schedule last refreshed at build time: <time datetime=\""
        + generated_at
        + "\">"
        + generated_at
        + "</time></p>"
        + "".join(sections)
        + "<h2>Last published source status</h2><pre>"
        + status
        + "</pre>"
    )


def write_sports_guide(stage, events: Iterable[SportsEvent], source_status: dict[str, str], *, generated_at: datetime, playlist_names: Iterable[str]) -> dict[str, Any]:
    """Write the companion schedule artifacts without modifying the M3U/EPG."""
    payload = event_payload(events, source_status, generated_at=generated_at, playlist_names=playlist_names)
    stage.joinpath("sports-guide.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    stage.joinpath("sports-guide.html").write_text(render_html(payload), encoding="utf-8")
    return payload
