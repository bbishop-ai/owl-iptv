import json
from pathlib import Path


def test_checked_in_superset_is_complete():
    root = Path(__file__).parents[1]
    inventory = json.loads((root / "audit/upstream_inventory.json").read_text(encoding="utf-8"))
    configured = json.loads((root / "config/sources.json").read_text(encoding="utf-8"))["sources"]
    audited = {item["url"] for item in inventory["occurrences"]}
    assert audited <= {item["url"] for item in configured}
    assert len(configured) == len({item["url"] for item in configured})


def test_known_failing_epg_endpoints_are_not_enabled():
    root = Path(__file__).parents[1]
    configured = json.loads((root / "config/sources.json").read_text(encoding="utf-8"))["sources"]
    enabled_urls = {item["url"] for item in configured if item.get("enabled", True)}

    assert "https://worker-9dd4.onrender.com/guide.xml.gz" not in enabled_urls
    assert "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_epg.xml" not in enabled_urls


def test_suspended_render_epg_is_preserved_but_disabled():
    root = Path(__file__).parents[1]
    configured = json.loads((root / "config/sources.json").read_text(encoding="utf-8"))["sources"]
    inventory = json.loads((root / "audit/upstream_inventory.json").read_text(encoding="utf-8"))["canonical_sources"]
    by_url = {item["url"]: item for item in configured}
    inventory_by_url = {item["url"]: item for item in inventory}
    render_url = "https://worker-9dd4.onrender.com/guide.xml.gz"

    render_source = by_url[render_url]
    assert render_source["enabled"] is False
    assert inventory_by_url[render_url]["enabled"] is False
    assert "iptv-org/epg GUIDES.md" in render_source["provenance"]
    assert render_url in render_source["declared_urls"]


def test_tubi_epg_uses_verified_replacement_and_preserves_provenance():
    root = Path(__file__).parents[1]
    configured = json.loads((root / "config/sources.json").read_text(encoding="utf-8"))["sources"]
    by_url = {item["url"]: item for item in configured}
    replacement_url = "https://raw.githubusercontent.com/BuddyChewChew/tubi-scraper/refs/heads/main/tubi_epg.xml"
    old_url = "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_epg.xml"

    replacement = by_url[replacement_url]
    assert replacement["enabled"] is True
    assert replacement["id"] == "6dff8a142750"
    assert old_url in replacement["declared_urls"]


def test_audit_regeneration_preserves_suspended_render_epg_as_disabled(monkeypatch, tmp_path):
    from tools import audit_upstreams

    render_url = "https://worker-9dd4.onrender.com/guide.xml.gz"
    grouped_render = {
        "repo": "cs3306/IPTV-sources",
        "declared_url": render_url,
        "url": render_url,
        "path": "config.json:epg_urls",
        "kind": "epg",
        "cloud_compatible": True,
    }
    monkeypatch.setattr(audit_upstreams, "extract_cs", lambda root: [grouped_render])
    monkeypatch.setattr(audit_upstreams, "extract_herbert", lambda root: [])
    monkeypatch.setattr(audit_upstreams, "extract_walke", lambda root: [])
    monkeypatch.setattr(audit_upstreams, "git_sha", lambda root: "test-sha")
    roots = {repo: tmp_path for repo in audit_upstreams.REPOSITORIES}

    audit_upstreams.build(roots, tmp_path)

    generated = json.loads((tmp_path / "config/sources.json").read_text(encoding="utf-8"))["sources"]
    by_url = {item["url"]: item for item in generated}
    render_url = "https://worker-9dd4.onrender.com/guide.xml.gz"

    render_source = by_url[render_url]
    assert sum(item["url"] == render_url for item in generated) == 1
    assert render_source["enabled"] is False
    assert render_source["id"] == "9785cbf18059"
    assert "iptv-org/epg GUIDES.md" in render_source["provenance"]
    assert "cs3306/IPTV-sources" in render_source["provenance"]
    assert render_url in render_source["declared_urls"]

    replacement_url = "https://raw.githubusercontent.com/BuddyChewChew/tubi-scraper/refs/heads/main/tubi_epg.xml"
    old_tubi_url = "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_epg.xml"
    tubi_source = by_url[replacement_url]
    assert tubi_source["enabled"] is True
    assert tubi_source["id"] == "6dff8a142750"
    assert old_tubi_url in tubi_source["declared_urls"]

