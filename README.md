# Owl IPTV

A standalone, set-and-forget, GitHub-hosted M3U + XMLTV pipeline for Owl IP Player. It builds a canonical superset of curated IPTV source definitions, filters for configured languages, validates streams, collapses conservative duplicates, ranks primary/backup choices, matches EPG, and deploys only a sane fresh build.

It does not supply credentials, decrypt DRM, bypass paywalls, or turn LAN-only multicast into public streams. Use only streams you are entitled to access.

## Stable URLs

After GitHub Pages is enabled for **GitHub Actions**, use:

- Playlist: `https://bbishop-ai.github.io/owl-iptv/playlist.m3u`
- XMLTV: `https://bbishop-ai.github.io/owl-iptv/epg.xml`
- Build stats: `https://bbishop-ai.github.io/owl-iptv/stats.json`

The M3U includes both `url-tvg` and `x-tvg-url`, but Owl's current public listing only documents adding an XMLTV source separately; it does not promise automatic header discovery. In Owl, add the playlist URL, then add the XMLTV URL as its EPG source. This is the reliable setup.

## Pipeline

`sources → fetch/cache → parse → normalize metadata → classify language → filter → staged validation → quality analysis → identity normalization → conservative dedupe → rank → primary/backup → EPG match → generate → sanity check → atomic Pages deploy`

Every scheduled run builds in a staging directory. Failed downloads can use cached source bodies, validation results are reused for 24 hours, media inspection is capped, and deployment only begins after tests and sanity checks. A failed build leaves the prior Pages deployment untouched.

## Configuration

Edit `config/pipeline.yml`:

- `filter.languages`: ISO-639-1 codes; defaults to `[en]`.
- `filter.countries.prefer`: defaults to `[US]` and influences duplicate ranking.
- `filter.allow_non_us_english`: set to `false`, keeping the published feed limited to US channels.
- `filter.allow_unknown`: defaults to `false`. Unknown-language rows are counted and excluded rather than silently treated as English.
- `validation.deep_probe_limit`: limits expensive ffprobe/frozen-frame checks.
- `dedupe.backups_per_channel`: defaults to one backup per normalized identity.
- `publish.minimum_channels`: production floor. Raise this after the first successful run to detect severe regressions.
- `publish.epg_url`: the stable URL embedded in the M3U header.

`config/sources.json` is generated from the audit. Each canonical input records all declaring repositories and original wrapped URLs. Cloud-incompatible discovery and multicast inputs remain inventoried but disabled; this is explicit, not omission.

## Upstream audit and adding sources

The checked-in audit records repository commit SHAs, every operational URL occurrence, the canonical source list, and the exact set difference. To reproduce against current upstream heads:

```sh
python -m tools.audit_upstreams --output .
pytest -q
```

For a deliberate local-only source, add a unique record to `config/sources.json`. For changes originating in any of the three audited repositories, rerun the audit instead of editing generated entries. Review `audit/source_diff.json` and commit all three generated files together.

## GitHub setup

1. Open **Settings → Pages** and choose **GitHub Actions** as the source.
2. Open **Actions → Build and publish IPTV → Run workflow** for the first build.
3. Confirm the Pages deployment and `stats.json`. The checked-in production floor is 500 logical streams, below the verified baseline but high enough to reject an empty or severely degraded build.
4. Add the stable playlist and XMLTV URLs to Owl.

The workflow runs at minute 17 every six hours and also supports manual dispatch. `refresh_audit` refreshes the three-repository source inventory during a manual run; normal scheduled runs keep the reviewed, repeatable config stable.

## Validation and ranking

Validation borrows the strongest practical ideas from the three bases: concurrent availability/latency checks, resolution probing, bounded download/media inspection, cached results, and normalized-name grouping. A five-second sampled frame hash flags a truly static video sample as frozen. Ranking prefers playable, non-frozen, higher-resolution, higher-frame-rate, HTTPS, lower-latency streams. Exact URL variants are removed first; channel identities use `tvg-id` when present and a conservative normalized name otherwise. One primary plus one backup is published where available.

EPG matching is deterministic: exact `tvg-id`, collision-safe normalization of known provider ID suffixes, unique normalized-name match, then only an unambiguous high-threshold fuzzy match. The EPG inputs include the available IPTV-org guide, the merged US national/local/sports guide, and dedicated public Plex and Pluto US guides. The published XMLTV contains only matched channels and their programmes, keeping it smaller for a TV device.

## Troubleshooting

- **Owl shows channels but no guide:** add `epg.xml` separately in Owl. Confirm channel `tvg-id` values appear as XMLTV channel IDs.
- **Too many non-English channels:** set `allow_unknown: false`. Many source playlists omit reliable language tags, so this trades recall for precision.
- **Too few channels:** inspect `stats.json` source failures and validation counts. Do not lower the sanity floor until the cause is understood.
- **A run fails:** the last Pages deployment remains live. Rerun manually; source bodies and recent validations are cached.
- **FFmpeg timeouts:** reduce `deep_probe_limit` or validation concurrency. Cheap availability checks still cover all candidate URLs.
- **Multicast streams absent:** the walke2019 maps are LAN/provider-network inputs and intentionally disabled on GitHub runners.

## Repository map

- `src/iptv_aggregator/sources.py` — conditional fetch and cache
- `metadata.py` — authoritative IPTV-org country/language enrichment
- `parse.py`, `normalize.py` — playlist parsing, metadata, language and identity
- `validator.py` — availability, latency, resolution, FPS and frozen video
- `dedupe.py` — conservative grouping and quality ranking
- `epg.py` — XMLTV aggregation and matching
- `publish.py`, `pipeline.py` — artifact generation, sanity checks and atomic local swap
- `tools/audit_upstreams.py` — repeatable source inventory/diff generator
- `audit/` — commit-pinned inventory, diff, and human summary
