#!/usr/bin/python3
"""Forced SSH command: publish one Windows helper installer to pearnly.com/downloads/companion/.

The CI caller (`.github/workflows/companion-release.yml`) sends one JSON line, then exactly
`size` bytes of the installer. Nothing else is accepted: no shell, no path, no file name.

- staging: keeps the file under `staging/<publication>/<source>/` and changes nothing users see;
- prod:    writes the immutable `releases/<version>/<source>/PearnlyCompanion-Setup.exe`, then
           replaces the fixed-name download and `latest.json` (the helper's update check) atomically.

A prod version can never go backwards, and an existing release path is never overwritten with
different bytes. Staging copies older than seven days are removed on each call.
"""
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/var/www/pearnly-downloads/companion")
NAME = "PearnlyCompanion-Setup.exe"
URL = "/downloads/companion"
MAX_BYTES = 300 * 1024 * 1024
STAGING_DAYS = 7


def version_tuple(value):
    return tuple(int(part) for part in value.split("."))


def fail(message):
    raise SystemExit(message)


def read_manifest():
    line = sys.stdin.buffer.readline(4096)
    try:
        manifest = json.loads(line)
    except ValueError:
        fail("Manifest line is not JSON")
    if not isinstance(manifest, dict):
        fail("Manifest must be an object")
    checks = {
        "target": r"prod|staging",
        "version": r"\d{1,4}(\.\d{1,4}){2,3}",
        "source_sha": r"[0-9a-f]{40}",
        "sha256": r"[0-9a-f]{64}",
        "publication": r"\d{1,20}-\d{1,4}",
    }
    for key, pattern in checks.items():
        if not isinstance(manifest.get(key), str) or not re.fullmatch(pattern, manifest[key]):
            fail(f"Invalid {key}")
    if not isinstance(manifest.get("size"), int) or not 0 < manifest["size"] <= MAX_BYTES:
        fail("Invalid size")
    return manifest


def read_installer(manifest, directory):
    digest, remaining = hashlib.sha256(), manifest["size"]
    path = Path(directory) / NAME
    with open(path, "wb") as handle:
        while remaining:
            chunk = sys.stdin.buffer.read(min(remaining, 1024 * 1024))
            if not chunk:
                fail("Installer shorter than its manifest")
            digest.update(chunk)
            handle.write(chunk)
            remaining -= len(chunk)
    if sys.stdin.buffer.read(1):
        fail("Installer longer than its manifest")
    if digest.hexdigest() != manifest["sha256"]:
        fail("Installer sha256 does not match its manifest")
    with open(path, "rb") as handle:
        if handle.read(2) != b"MZ":
            fail("Not a Windows executable")
    os.chmod(path, 0o644)
    return path


def place(source, target):
    """Copy into the target directory under a temporary name, then rename (atomic for readers)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".incoming-")
    os.close(fd)
    shutil.copyfile(source, temporary)
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)


def write_json(target, value):
    fd, temporary = tempfile.mkstemp(dir=target.parent, prefix=".incoming-")
    with os.fdopen(fd, "w") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.chmod(temporary, 0o644)
    os.replace(temporary, target)


def prune_staging():
    cutoff = time.time() - STAGING_DAYS * 86400
    for entry in (ROOT / "staging").glob("*"):
        if entry.is_dir() and entry.stat().st_mtime < cutoff:
            shutil.rmtree(entry)


def main():
    manifest = read_manifest()
    ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pearnly-companion-", dir=ROOT.parent) as directory:
        installer = read_installer(manifest, directory)
        source, version = manifest["source_sha"], manifest["version"]
        if manifest["target"] == "staging":
            relative = f"staging/{manifest['publication']}/{source}/{NAME}"
            place(installer, ROOT / relative)
            prune_staging()
            print(json.dumps({"target": "staging", "url": f"{URL}/{relative}", "sha256": manifest["sha256"]}))
            return
        relative = f"releases/{version}/{source}/{NAME}"
        release = ROOT / relative
        if release.exists():
            if hashlib.sha256(release.read_bytes()).hexdigest() != manifest["sha256"]:
                fail("This release path already holds different bytes")
        else:
            place(installer, release)
        latest_file = ROOT / "latest.json"
        if latest_file.exists():
            current = json.loads(latest_file.read_text()).get("version") or "0.0.0"
            if version_tuple(version) < version_tuple(current):
                fail(f"Refusing to go back from {current} to {version}")
        place(installer, ROOT / NAME)
        latest = {"version": version, "url": f"{URL}/{relative}?v={source}", "sha256": manifest["sha256"],
                  "size": manifest["size"], "source_sha": source,
                  "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        write_json(latest_file, latest)
        print(json.dumps({"target": "prod", **latest}))


if __name__ == "__main__":
    main()
