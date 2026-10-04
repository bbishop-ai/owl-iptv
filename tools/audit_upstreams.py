from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

REPOSITORIES = {
    "cs3306/IPTV-sources": "https://github.com/cs3306/IPTV-sources.git",
    "HerbertHe/iptv-sources": "https://github.com/HerbertHe/iptv-sources.git",
    "walke2019/iptv-api-two": "https://github.com/walke2019/iptv-api-two.git",
}
URL_RE = re.compile(r"https?://[^\"'\s<>]+")
PROXY_MARKERS = ("/https://", "/http://")


def canonical_url(value: str) -> str:
    value = unquote(value.strip().rstrip(",);"))
    for marker in PROXY_MARKERS:
        pos = value.find(marker, value.find("://") + 3)
        if pos >= 0:
            return value[pos + 1 :]
    return value


def record(repo: str, declared_url: str, path: str, kind: str, cloud_compatible: bool = True):
    url = canonical_url(declared_url)
    return {
        "repo": repo, "declared_url": declared_url, "url": url, "path": path,
        "kind": kind, "cloud_compatible": cloud_compatible,
    }


def extract_cs(root: Path):
    cfg = json.loads((root / "config.json").read_text(encoding="utf-8"))
    rows = [record("cs3306/IPTV-sources", url, "config.json:sources", "playlist") for url in cfg["sources"]]
    rows += [record("cs3306/IPTV-sources", url, "config.json:epg_urls", "epg") for url in cfg.get("epg_urls", [])]
    return rows


def extract_herbert(root: Path):
    rows = []
    for path in sorted((root / "src" / "sources").glob("*.ts")):
        for url in URL_RE.findall(path.read_text(encoding="utf-8")):
            multicast = "multicast" in url.lower()
            rows.append(record("HerbertHe/iptv-sources", url, path.relative_to(root).as_posix(), "playlist", not multicast))
    epg_path = root / "src" / "epgs" / "index.ts"
    for url in URL_RE.findall(epg_path.read_text(encoding="utf-8")):
        rows.append(record("HerbertHe/iptv-sources", url, epg_path.relative_to(root).as_posix(), "epg"))
    return rows


def extract_walke(root: Path):
    rows = []
    sub = root / "config" / "subscribe.txt"
    for line in sub.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith(("http://", "https://")):
            rows.append(record("walke2019/iptv-api-two", line.strip(), sub.relative_to(root).as_posix(), "playlist"))
    mmap = root / "updates" / "multicast" / "multicast_map.json"
    for region in json.loads(mmap.read_text(encoding="utf-8")).values():
        for url in region.values():
            rows.append(record("walke2019/iptv-api-two", url, mmap.relative_to(root).as_posix(), "playlist", False))
    discovery_paths = [root / "source.json", root / "updates" / "fofa" / "fofa_map.py", root / "utils" / "constants.py", root / "updates" / "multicast" / "update_tmp.py"]
    for path in discovery_paths:
        for url in URL_RE.findall(path.read_text(encoding="utf-8")):
            rows.append(record("walke2019/iptv-api-two", url, path.relative_to(root).as_posix(), "discovery", False))
    return rows


def git_sha(root: Path) -> str:
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def clone_all(target: Path) -> dict[str, Path]:
    roots = {}
    for repo, url in REPOSITORIES.items():
        dest = target / repo.split("/")[0].lower()
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", url, str(dest)], check=True)
        roots[repo] = dest
    return roots


def build(roots: dict[str, Path], output_root: Path):
    occurrences = extract_cs(roots["cs3306/IPTV-sources"]) + extract_herbert(roots["HerbertHe/iptv-sources"]) + extract_walke(roots["walke2019/iptv-api-two"])
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in occurrences:
        grouped[item["url"]].append(item)
    sources = []
    for url, items in sorted(grouped.items()):
        kinds = {i["kind"] for i in items}
        kind = "epg" if kinds == {"epg"} else "playlist" if "playlist" in kinds else "discovery"
        compatible = any(i["cloud_compatible"] for i in items)
        sources.append({
            "id": hashlib.sha256(url.encode()).hexdigest()[:12], "url": url, "kind": kind,
            "enabled": compatible and kind in {"playlist", "epg"}, "cloud_compatible": compatible,
            "provenance": sorted({i["repo"] for i in items}),
            "declared_urls": sorted({i["declared_url"] for i in items}),
        })
    # Curated English EPG guides; separated from the audited three-repo superset.
    additions = [
        {
            "url": "https://worker-9dd4.onrender.com/guide.xml.gz",
            "enabled": False,
            "provenance": ["iptv-org/epg GUIDES.md"],
            "declared_urls": ["https://worker-9dd4.onrender.com/guide.xml.gz"],
        },
        {
            "url": "https://vcicio.github.io/US-EPG/merged_epg.xml.gz",
            "enabled": True,
            "provenance": ["US-EPG (built from iptv-org/epg-listed EPGshare inputs)"],
            "declared_urls": ["https://vcicio.github.io/US-EPG/merged_epg.xml.gz"],
        },
        {
            "url": "https://raw.githubusercontent.com/BuddyChewChew/tubi-scraper/refs/heads/main/tubi_epg.xml",
            "enabled": True,
            "provenance": ["BuddyChewChew Tubi guide", "BuddyChewChew/tubi-scraper replacement"],
            "declared_urls": [
                "https://raw.githubusercontent.com/BuddyChewChew/tubi-scraper/refs/heads/main/tubi_epg.xml",
                "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_epg.xml",
            ],
        },
    ]
    source_by_url = {source["url"]: source for source in sources}
    for addition in additions:
        url = addition["url"]
        source = source_by_url.get(url)
        if source is None:
            source = {
                "id": hashlib.sha256(url.encode()).hexdigest()[:12],
                "url": url,
                "kind": "epg",
                "enabled": addition["enabled"],
                "cloud_compatible": True,
                "provenance": addition["provenance"],
                "declared_urls": addition["declared_urls"],
            }
            sources.append(source)
            source_by_url[url] = source
            continue
        source.update({
            "kind": "epg",
            "enabled": addition["enabled"],
            "cloud_compatible": True,
            "provenance": sorted(set(source["provenance"]) | set(addition["provenance"])),
            "declared_urls": sorted(set(source["declared_urls"]) | set(addition["declared_urls"])),
        })

    repo_sets = {repo: sorted({o["url"] for o in occurrences if o["repo"] == repo}) for repo in REPOSITORIES}
    base = set(repo_sets["cs3306/IPTV-sources"])
    other = set(repo_sets["HerbertHe/iptv-sources"]) | set(repo_sets["walke2019/iptv-api-two"])
    diff = {
        "base_count": len(base), "other_union_count": len(other),
        "missing_from_base": sorted(other - base), "already_in_base": sorted(base & other),
        "superset_count": len(base | other), "config_contains_all_audited": set(grouped).issubset({s["url"] for s in sources}),
    }
    all_sets = {repo: set(values) for repo, values in repo_sets.items()}
    diff["shared_by_multiple_projects"] = sorted(url for url in grouped if sum(url in values for values in all_sets.values()) > 1)
    diff["unique_by_project"] = {
        repo: sorted(values - set().union(*(other_values for other_repo, other_values in all_sets.items() if other_repo != repo)))
        for repo, values in all_sets.items()
    }
    inventory = {"schema": 1, "repositories": {repo: {"url": REPOSITORIES[repo], "commit": git_sha(root)} for repo, root in roots.items()}, "occurrences": occurrences, "canonical_sources": sources, "counts_by_repo": {repo: len(values) for repo, values in repo_sets.items()}}
    (output_root / "audit").mkdir(parents=True, exist_ok=True)
    (output_root / "config").mkdir(parents=True, exist_ok=True)
    (output_root / "audit" / "upstream_inventory.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "audit" / "source_diff.json").write_text(json.dumps(diff, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_root / "config" / "sources.json").write_text(json.dumps({"sources": sources}, ensure_ascii=False, indent=2), encoding="utf-8")
    md = ["# Upstream source audit", "", "Generated from operational code/config, not README lists.", "", "| Repository | Commit | Occurrences |", "|---|---|---:|"]
    for repo, details in inventory["repositories"].items():
        md.append(f"| `{repo}` | `{details['commit']}` | {inventory['counts_by_repo'][repo]} |")
    md += ["", f"Canonical audited superset: **{diff['superset_count']}** sources.", f"Sources added beyond cs3306: **{len(diff['missing_from_base'])}**.", f"Shared by multiple projects: **{len(diff['shared_by_multiple_projects'])}**."]
    for repo, values in diff["unique_by_project"].items():
        md.append(f"Unique to `{repo}`: **{len(values)}**.")
    md += [f"Completeness assertion: **{diff['config_contains_all_audited']}**.", "", "Machine-readable evidence (including every URL list): `upstream_inventory.json` and `source_diff.json`."]
    (output_root / "audit" / "UPSTREAMS.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    if not diff["config_contains_all_audited"]:
        raise SystemExit("superset completeness check failed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cs3306", type=Path)
    parser.add_argument("--herberthe", type=Path)
    parser.add_argument("--walke2019", type=Path)
    parser.add_argument("--output", type=Path, default=Path.cwd())
    args = parser.parse_args()
    if all((args.cs3306, args.herberthe, args.walke2019)):
        roots = {"cs3306/IPTV-sources": args.cs3306, "HerbertHe/iptv-sources": args.herberthe, "walke2019/iptv-api-two": args.walke2019}
        build(roots, args.output)
    else:
        with tempfile.TemporaryDirectory() as directory:
            build(clone_all(Path(directory)), args.output)


if __name__ == "__main__":
    main()
