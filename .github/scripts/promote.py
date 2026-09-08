#!/usr/bin/env python3
"""SAMSKARA Project Promotion CD.

Reads this checked-out repo's synced files (exactly the set Git
Integration understands: notebooks/*.ipynb + the known top-level JSON
files) and POSTs them to the target SAMSKARA deployment's promotion
import endpoint. Stdlib only -- no pip install step, matching this
org's "CI scripts run with zero external dependencies" convention
(learned the hard way once already: a migration script that needed
httpx broke on a VPS with no pip access).

CI checks out exactly the approved commit and sends exactly those
files -- Prod never independently fetches GitHub itself. That's the
whole security property this endpoint is built around; this script's
only job is to preserve it faithfully.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = REPO_ROOT / "samskara-cd.yml"

KNOWN_TOP_LEVEL_FILES = (
    "schedules.json",
    "runtime_profiles.json",
    "dashboards.json",
    "streaming_profiles.json",
    "storage_profiles.json",
    "database_connections.json",
    "arrowlake_profiles.json",
)


def _parse_simple_yaml(path: Path) -> dict[str, str]:
    """Flat `key: value` parser -- no nesting, no lists, no quoting
    edge cases beyond simple wrapping quotes. Avoids a PyYAML dependency
    for what is, today, a two-key config file."""
    config: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        config[key.strip()] = value.strip().strip('"').strip("'")
    return config


def _gather_files() -> dict[str, str]:
    files: dict[str, str] = {}
    notebooks_dir = REPO_ROOT / "notebooks"
    if notebooks_dir.is_dir():
        for p in sorted(notebooks_dir.glob("*.ipynb")):
            files[f"notebooks/{p.name}"] = p.read_text(encoding="utf-8")
    for name in KNOWN_TOP_LEVEL_FILES:
        p = REPO_ROOT / name
        if p.is_file():
            files[name] = p.read_text(encoding="utf-8")
    return files


def main() -> None:
    if not CONFIG_PATH.is_file():
        print(f"::error::{CONFIG_PATH.name} not found at repo root", file=sys.stderr)
        sys.exit(1)

    config = _parse_simple_yaml(CONFIG_PATH)
    project_id = config.get("project_id")
    if not project_id:
        print("::error::samskara-cd.yml is missing project_id", file=sys.stderr)
        sys.exit(1)
    activate = config.get("activate_imported_schedules", "false").lower() == "true"

    base_url = os.environ["SAMSKARA_BASE_URL"].rstrip("/")
    token = os.environ["SAMSKARA_PROMOTION_TOKEN"]
    source_ref = os.environ["SOURCE_REF"]
    source_commit = os.environ["SOURCE_COMMIT"]

    files = _gather_files()
    if not files:
        print(
            "::error::No recognized files found to promote "
            "(notebooks/*.ipynb or the known top-level *.json files)",
            file=sys.stderr,
        )
        sys.exit(1)

    payload = {
        "source_repo": "ganesh-2014/samskara-pilot",
        "source_ref": source_ref,
        "source_commit": source_commit,
        "files": files,
        "activate_imported_schedules": activate,
    }
    body = json.dumps(payload).encode("utf-8")

    url = f"{base_url}/api/promotion/projects/{project_id}/import"
    print(f"Promoting {len(files)} file(s) from {source_ref} ({source_commit[:7]}) -> {url}")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read())
            print(f"Promotion succeeded:\n{json.dumps(result, indent=2)}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.dumps(json.loads(detail), indent=2)
        except Exception:
            pass
        print(f"::error::Promotion failed (HTTP {exc.code}):\n{detail}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"::error::Could not reach {url}: {exc.reason}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
