from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .config import load_config, load_sources
from .dedupe import preselect_candidates, select_streams
from .epg import fetch_epg, match_channels, xmltv_bytes
from .metadata import load_metadata
from .models import Validation
from .normalize import language_allowed, normalize_channel, normalized_name
from .publish import atomic_publish, m3u_text, sanity_check
from .sources import SourceFetcher
from .validator import Validator


async def run(config_path: str) -> dict:
    cfg = load_config(config_path)
    root = Path(cfg["_root"])
    sources = load_sources(root / cfg["sources_file"])
    work = root / "build"
    stage = root / "stage"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    fetcher = SourceFetcher(work / "source-cache", cfg["fetch"]["timeout_seconds"], cfg["fetch"]["concurrency"], root)
    fetched = await fetcher.fetch_all(sources)
    channels = [channel for _, items, _ in fetched for channel in items]
    raw_count = len(channels)
    catalog, metadata_status = await load_metadata(cfg["metadata"]["url"], work / "iptv-org-channels.json", cfg["metadata"]["timeout_seconds"])
    metadata_methods = Counter(catalog.enrich(channel) for channel in channels)
    channels = [normalize_channel(channel) for channel in channels]
    fixture_path = Path(cfg["_root"]) / cfg["validation"].get("responsive_fixture", "")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8")) if fixture_path.is_file() else {}
    fixture_urls = set(fixture.get("statuses", {})) | set(fixture.get("preserve_urls", []))
    fixture_source_id = cfg["validation"].get("responsive_fixture_source_id")
    language_counts = Counter(channel.language for channel in channels)
    confirmed_english = language_counts["en"]
    confirmed_non_english = sum(count for language, count in language_counts.items() if language not in {"en", "unknown"})
    unknown_language = language_counts["unknown"]
    channels = [c for c in channels if language_allowed(c, cfg["filter"]["languages"], cfg["filter"]["allow_unknown"]) or (c.source_id == fixture_source_id and c.url in fixture_urls)]
    if not cfg["filter"]["allow_non_us_english"]:
        preferred = {value.upper() for value in cfg["filter"]["countries"]["prefer"]}
        channels = [c for c in channels if c.country.upper() in preferred or (c.source_id == fixture_source_id and c.url in fixture_urls)]
    english_count = len(channels)
    channels, prevalidation_removed = preselect_candidates(
        channels,
        cfg["validation"]["candidates_per_identity"],
        cfg["validation"]["total_candidate_limit"],
        cfg["validation"].get("candidates_per_identity_by_source"),
    )
    validator = Validator(work / "validation-cache.json", cfg["validation"]["timeout_seconds"], cfg["validation"]["concurrency"], cfg["validation"]["cache_ttl_hours"], cfg["validation"]["deep_probe_limit"])
    skip_sources = {source["id"] for source in sources if source.get("skip_validation")}
    soft_sources = {source["id"] for source in sources if source.get("soft_validation")}
    skip_urls = {c.url for c in channels if c.source_id in skip_sources}
    soft_urls = {c.url for c in channels if c.source_id in soft_sources} - skip_urls
    validations = await validator.validate(channels, skip_urls, soft_urls)
    fixture_path = Path(cfg["_root"]) / cfg["validation"].get("responsive_fixture", "")
    if fixture_path.is_file():
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        fixture_checked_at = fixture.get("checked_at", "comparison probe fixture")
        for channel in channels:
            status = fixture.get("statuses", {}).get(channel.url)
            if channel.source_id == fixture_source_id and (status in {200, 206} or channel.url in fixture.get("preserve_urls", [])):
                validations[channel.url] = Validation(
                    ok=True,
                    checked_at=fixture_checked_at,
                    error=f"comparison fixture HTTP {status}",
                )
    selected, dedupe_stats = select_streams(
        channels,
        validations,
        cfg["dedupe"]["backups_per_channel"],
        cfg["filter"]["countries"]["prefer"],
        cfg["dedupe"].get("backups_per_source"),
    )
    epg_names = set()
    for channel in selected:
        values = [channel.name, channel.tvg_name, channel.attrs.get("metadata-name", "")]
        try:
            values.extend(json.loads(channel.attrs.get("metadata-alt-names", "[]")))
        except json.JSONDecodeError:
            pass
        epg_names.update(normalized_name(value) for value in values if value)
    epg, epg_sources = await fetch_epg(sources, cfg["epg"]["timeout_seconds"], {c.tvg_id for c in selected if c.tvg_id}, epg_names)
    epg_stats = match_channels(selected, epg, cfg["epg"]["fuzzy_threshold"])
    source_status = {source["id"]: status for source, _, status in fetched}
    validation_counts = Counter("passed" if value.ok else "failed" for value in validations.values())
    stats = {
        "generated_at": datetime.now(UTC).isoformat(),
        "upstreams_attempted": len(fetched),
        "upstreams_succeeded": sum(not status.startswith("failed") for status in source_status.values()),
        "source_status": source_status,
        "metadata_status": metadata_status,
        "metadata_matches_by_method": dict(metadata_methods),
        "epg_source_status": epg_sources,
        "raw_channels": raw_count,
        "streams_parsed": raw_count,
        "confirmed_english": confirmed_english,
        "confirmed_non_english": confirmed_non_english,
        "unknown_language": unknown_language,
        "unknown_language_excluded": 0 if cfg["filter"]["allow_unknown"] else unknown_language,
        "language_kept": english_count,
        "validation_candidates": len(channels),
        "prevalidation_candidates_removed": prevalidation_removed,
        "validation_passed": validation_counts["passed"],
        "validation_failed": validation_counts["failed"],
        **dedupe_stats,
        **epg_stats,
        "published_streams": len(selected),
        "published_primary": sum(c.role == "primary" for c in selected),
        "published_backups": sum(c.role == "backup" for c in selected),
        "final_logical_channels": sum(c.role == "primary" for c in selected),
        "final_us_channels": sum(c.role == "primary" and c.country.upper() == "US" for c in selected),
        "final_non_us_english_channels": sum(c.role == "primary" and c.language == "en" and c.country.upper() != "US" for c in selected),
    }
    stats["duplicates_detected"] = stats["prevalidation_candidates_removed"] + stats["exact_url_duplicates_removed"] + stats["identity_duplicates_removed"]
    matched = stats["epg_exact_id"] + stats["epg_normalized_id"] + stats["epg_exact_name"] + stats["epg_fuzzy"]
    stats["epg_matched"] = matched
    stats["epg_matched_logical_channels"] = sum(c.role == "primary" and c.tvg_id in epg.channels for c in selected)
    stats["epg_coverage_percent"] = round(100 * stats["epg_matched_logical_channels"] / max(stats["final_logical_channels"], 1), 2)
    stage.joinpath("playlist.m3u").write_text(m3u_text(selected, cfg["publish"]["epg_url"], validations), encoding="utf-8")
    stage.joinpath("epg.xml").write_bytes(xmltv_bytes(selected, epg))
    stage.joinpath("stats.json").write_text(json.dumps(stats, indent=2, sort_keys=True), encoding="utf-8")
    stage.joinpath("report.md").write_text(_report(stats), encoding="utf-8")
    stage.joinpath("index.html").write_text(_index(stats), encoding="utf-8")
    stage.joinpath(".nojekyll").write_text("", encoding="utf-8")
    sanity_check(stage, cfg["publish"]["minimum_channels"])
    atomic_publish(stage, root / cfg["publish"]["directory"])
    return stats


def _index(stats: dict) -> str:
    return "<!doctype html><meta charset=utf-8><title>Owl IPTV</title><h1>Owl IPTV</h1><ul><li><a href=playlist.m3u>Playlist</a></li><li><a href=epg.xml>EPG</a></li><li><a href=stats.json>Build stats</a></li><li><a href=report.md>Build report</a></li></ul><pre>" + json.dumps(stats, indent=2) + "</pre>"


def _report(stats: dict) -> str:
    keys = ["upstreams_attempted", "upstreams_succeeded", "raw_channels", "streams_parsed", "confirmed_english", "confirmed_non_english", "unknown_language", "unknown_language_excluded", "final_us_channels", "final_non_us_english_channels", "validation_passed", "validation_failed", "duplicates_detected", "final_logical_channels", "published_backups", "epg_exact_id", "epg_normalized_id", "epg_exact_name", "epg_fuzzy", "epg_unmatched", "epg_coverage_percent"]
    lines = ["# Latest Owl IPTV build", "", f"Generated: {stats['generated_at']}", "", "| Metric | Value |", "|---|---:|"]
    lines += [f"| {key.replace('_', ' ')} | {stats[key]} |" for key in keys]
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config/pipeline.yml")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.config)), indent=2))


if __name__ == "__main__":
    main()
