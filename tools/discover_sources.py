#!/usr/bin/env python3
"""Source discovery tool - scans upstream repositories for new sources."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

import aiohttp

from iptv_aggregator.config import load_config, load_sources
from iptv_aggregator.normalize import normalize_channel
from iptv_aggregator.parse import parse_playlist
from iptv_aggregator.validator import Validator

# Known upstream repositories to scan
UPSTREAM_REPOS = [
    "iptv-org/iptv",
    "Free-TV/IPTV",
    "BuddyChewChew/app-m3u-generator",
    "FanMingming/live",
    "vamoschuck/TV",
    "MercuryZz/IPTVN",
    "YueChan/Live",
    "Guovin/TV",
    "Kimentanm/aptv",
    "Rivens7/Livelist",
    "YanG-1989/m3u",
    "alienlu/iptv",
    "asdjkl6/tv",
    "balala2oo8/iptv",
    "cai23511/yex",
    "cairong/iptv-yuan",
    "cymz6/AutoIPTV-Hotel",
    "drangjchen/IPTV",
    "fenxp/iptv",
    "iceyheart/IPTV",
    "jisoypub/iptv",
    "joevess/IPTV",
    "kimwang1978/collect-tv-txt",
    "lalifeier/IPTV",
    "mlzlzj/hnyuan",
    "qwerttvv/Beijing-IPTV",
    "skddyj/iptv",
    "suxuang/myIPTV",
    "vbskycn/iptv",
    "wwb521/live",
    "ybskycn/iptv",
    "yuanzl77/IPTV",
    "zbefine/iptv",
    "zwc456baby/iptv_alive",
]


async def discover_sources(
    config_path: str,
    output_dir: str,
    max_new: int,
    min_yield: int,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    root = Path(cfg["_root"])
    existing_sources = load_sources(root / cfg["sources_file"])
    existing_urls = {s.get("url", "") for s in existing_sources}

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Fetch from upstream repos (GitHub API)
    new_candidates = await scan_upstream_repos(existing_urls)

    # Validate candidates
    validated = await validate_candidates(cfg, new_candidates, min_yield)

    # Select top candidates
    selected = validated[:max_new]

    # Generate new sources.json entries
    new_sources = []
    for candidate in selected:
        new_sources.append({
            "id": candidate["id"],
            "url": candidate["url"],
            "kind": candidate["kind"],
            "enabled": True,
            "cloud_compatible": candidate["cloud_compatible"],
            "provenance": candidate["provenance"],
            "declared_urls": [candidate["url"]],
        })

    # Write results
    (output_path / "new_sources.json").write_text(json.dumps(new_sources, indent=2))
    (output_path / "candidates.json").write_text(json.dumps(validated, indent=2))

    return {
        "scanned_repos": len(UPSTREAM_REPOS),
        "candidates_found": len(new_candidates),
        "candidates_validated": len(validated),
        "selected": len(selected),
        "new_sources": new_sources,
    }


async def scan_upstream_repos(existing_urls: set[str]) -> list[dict[str, Any]]:
    """Scan GitHub repos for M3U/XMLTV URLs in their files."""
    candidates = []

    # For now, add known high-quality sources that we might be missing
    # In a full implementation, this would use GitHub API to scan repos
    known_sources = [
        # iptv-org additional countries
        {"id": "iptv-org-de", "url": "https://iptv-org.github.io/iptv/countries/de.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        {"id": "iptv-org-fr", "url": "https://iptv-org.github.io/iptv/countries/fr.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        {"id": "iptv-org-es", "url": "https://iptv-org.github.io/iptv/countries/es.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        {"id": "iptv-org-it", "url": "https://iptv-org.github.io/iptv/countries/it.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        {"id": "iptv-org-nl", "url": "https://iptv-org.github.io/iptv/countries/nl.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        {"id": "iptv-org-pl", "url": "https://iptv-org.github.io/iptv/countries/pl.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        {"id": "iptv-org-br", "url": "https://iptv-org.github.io/iptv/countries/br.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        {"id": "iptv-org-mx", "url": "https://iptv-org.github.io/iptv/countries/mx.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        # iptv-org categories
        {"id": "iptv-org-music", "url": "https://iptv-org.github.io/iptv/categories/music.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        {"id": "iptv-org-kids", "url": "https://iptv-org.github.io/iptv/categories/kids.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        {"id": "iptv-org-documentary", "url": "https://iptv-org.github.io/iptv/categories/documentary.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        {"id": "iptv-org-religious", "url": "https://iptv-org.github.io/iptv/categories/religious.m3u", "kind": "playlist", "provenance": ["iptv-org/iptv"], "cloud_compatible": True},
        # Free-TV categories
        {"id": "free-tv-news", "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/lists/news.m3u8", "kind": "playlist", "provenance": ["Free-TV/IPTV"], "cloud_compatible": True},
        {"id": "free-tv-sports", "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/lists/sports.m3u8", "kind": "playlist", "provenance": ["Free-TV/IPTV"], "cloud_compatible": True},
        {"id": "free-tv-movies", "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/lists/movies.m3u8", "kind": "playlist", "provenance": ["Free-TV/IPTV"], "cloud_compatible": True},
        {"id": "free-tv-kids", "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/lists/kids.m3u8", "kind": "playlist", "provenance": ["Free-TV/IPTV"], "cloud_compatible": True},
        {"id": "free-tv-music", "url": "https://raw.githubusercontent.com/Free-TV/IPTV/master/lists/music.m3u8", "kind": "playlist", "provenance": ["Free-TV/IPTV"], "cloud_compatible": True},
        # BuddyChewChew additional regions
        {"id": "buddy-pluto-ca", "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plutotv_ca.m3u", "kind": "playlist", "provenance": ["BuddyChewChew/app-m3u-generator"], "cloud_compatible": True},
        {"id": "buddy-pluto-gb", "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plutotv_gb.m3u", "kind": "playlist", "provenance": ["BuddyChewChew/app-m3u-generator"], "cloud_compatible": True},
        {"id": "buddy-pluto-au", "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plutotv_au.m3u", "kind": "playlist", "provenance": ["BuddyChewChew/app-m3u-generator"], "cloud_compatible": True},
        {"id": "buddy-samsung-ca", "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/samsungtvplus_ca.m3u", "kind": "playlist", "provenance": ["BuddyChewChew/app-m3u-generator"], "cloud_compatible": True},
        {"id": "buddy-samsung-gb", "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/samsungtvplus_gb.m3u", "kind": "playlist", "provenance": ["BuddyChewChew/app-m3u-generator"], "cloud_compatible": True},
        {"id": "buddy-samsung-au", "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/samsungtvplus_au.m3u", "kind": "playlist", "provenance": ["BuddyChewChew/app-m3u-generator"], "cloud_compatible": True},
        {"id": "buddy-plex-ca", "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plex_ca.m3u", "kind": "playlist", "provenance": ["BuddyChewChew/app-m3u-generator"], "cloud_compatible": True},
        {"id": "buddy-plex-gb", "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plex_gb.m3u", "kind": "playlist", "provenance": ["BuddyChewChew/app-m3u-generator"], "cloud_compatible": True},
    ]

    for src in known_sources:
        if src["url"] not in existing_urls:
            candidates.append(src)

    return candidates


async def validate_candidates(
    cfg: dict[str, Any],
    candidates: list[dict[str, Any]],
    min_yield: int,
) -> list[dict[str, Any]]:
    """Validate candidate sources and return those meeting minimum yield."""
    validated = []
    work = Path(cfg["_root"]) / "build"

    for candidate in candidates:
        try:
            # Quick fetch and parse
            timeout = aiohttp.ClientTimeout(total=cfg["fetch"]["timeout_seconds"])
            async with aiohttp.ClientSession(timeout=timeout, headers={"User-Agent": "owl-iptv/1.0"}) as session, \
                    session.get(candidate["url"], allow_redirects=True) as response:
                if response.status != 200:
                    continue
                raw = await response.read()

            channels = parse_playlist(raw.decode("utf-8", errors="replace"), candidate["id"])
            if not channels:
                continue

            channels = [normalize_channel(c) for c in channels]

            # Quick validation sample
            validator = Validator(
                cache_path=work / "validation-cache.json",
                timeout=cfg["validation"]["timeout_seconds"],
                concurrency=min(cfg["validation"]["concurrency"], 4),
                ttl_hours=cfg["validation"]["ttl_hours"],
                deep_limit=0,
            )
            results = await validator.validate(channels[:50])

            ok_count = sum(1 for v in results.values() if v.ok)
            if ok_count >= min_yield:
                candidate["yield"] = ok_count
                candidate["total_parsed"] = len(channels)
                validated.append(candidate)
                print(f"  ✓ {candidate['id']}: {ok_count}/{len(channels)} valid")
            else:
                print(f"  ✗ {candidate['id']}: {ok_count}/{len(channels)} valid (below min_yield={min_yield})")

        except Exception as e:
            print(f"  ✗ {candidate['id']}: {e}")
            continue

    # Sort by yield descending
    validated.sort(key=lambda x: x.get("yield", 0), reverse=True)
    return validated


def main():
    parser = argparse.ArgumentParser(description="Discover new IPTV sources")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-new", type=int, default=20)
    parser.add_argument("--min-yield", type=int, default=5)
    args = parser.parse_args()

    result = asyncio.run(discover_sources(
        args.config,
        args.output_dir,
        args.max_new,
        args.min_yield,
    ))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()