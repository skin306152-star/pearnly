#!/usr/bin/python3
"""Forced SSH command: publish one Windows helper installer to pearnly.com/downloads/companion/.

The CI caller (`.github/workflows/companion-release.yml`) sends one JSON line, then exactly
`size` bytes of the installer. Nothing else is accepted: no shell, no path, no file name.

- staging: keeps the file under `staging/<publication>/<source>/` and changes nothing users see;
- prod:    writes the immutable `releases/<version>/<source>/CC-Setup.exe`, then
           replaces the fixed-name download (`CC-Setup.exe`, plus the old `PearnlyCompanion-Setup.exe`
           so links handed out before the 2026-10-12 rename keep working) and `latest.json` atomically.

A prod version can never go backwards, and an existing release path is never overwritten with
different bytes. Staging copies older than seven days are removed on each call.

Optional cloud package (the slim CC for the cloud Express host, `release.ps1 -BuildOnly -Cloud`): when the manifest
line carries `cloud: {version, sha256, size}`, exactly `cloud.size` bytes of `CC-Cloud.zip` follow the installer.
It lands next to the installer (`releases/<version>/<source>/CC-Cloud.zip`, or the staging folder) and prod
`latest.json` gains `cloud: {version, url, sha256, size}` — the bridge CC downloads it from there and checks the sha256
before putting it on the tunnel drive (`companion/src/companion/cloud_link/cloud_package.py`). Without `cloud` the
call behaves exactly as before.
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
NAME = "CC-Setup.exe"
CLOUD_NAME = "CC-Cloud.zip"
LEGACY_NAME = "PearnlyCompanion-Setup.exe"  # 改名前发出去的固定下载链接
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
    cloud = manifest.get("cloud")
    if cloud is not None:
        if not isinstance(cloud, dict) or cloud.get("version") != manifest["version"]:
            fail("Invalid cloud")
        if not isinstance(cloud.get("sha256"), str) or not re.fullmatch(checks["sha256"], cloud["sha256"]):
            fail("Invalid cloud sha256")
        if not isinstance(cloud.get("size"), int) or not 0 < cloud["size"] <= MAX_BYTES:
            fail("Invalid cloud size")
    return manifest


def read_part(path, size, sha256, magic, what):
    """Read exactly ``size`` bytes of one file from stdin into ``path`` and check its sha256 and magic bytes."""
    digest, remaining = hashlib.sha256(), size
    with open(path, "wb") as handle:
        while remaining:
            chunk = sys.stdin.buffer.read(min(remaining, 1024 * 1024))
            if not chunk:
                fail(f"{what} shorter than its manifest")
            digest.update(chunk)
            handle.write(chunk)
            remaining -= len(chunk)
    if digest.hexdigest() != sha256:
        fail(f"{what} sha256 does not match its manifest")
    with open(path, "rb") as handle:
        if handle.read(len(magic)) != magic:
            fail(f"{what} has the wrong file type")
    os.chmod(path, 0o644)
    return path


def read_installer(manifest, directory):
    """The installer, then (when the manifest has ``cloud``) the cloud package; nothing may follow. Returns both paths."""
    installer = read_part(Path(directory) / NAME, manifest["size"], manifest["sha256"], b"MZ", "Installer")
    cloud = manifest.get("cloud")
    package = None
    if cloud is not None:
        package = read_part(Path(directory) / CLOUD_NAME, cloud["size"], cloud["sha256"], b"PK\x03\x04", "Cloud package")
    if sys.stdin.buffer.read(1):
        fail("Upload longer than its manifest")
    return installer, package


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
        installer, package = read_installer(manifest, directory)
        source, version = manifest["source_sha"], manifest["version"]
        if manifest["target"] == "staging":
            folder = f"staging/{manifest['publication']}/{source}"
            relative = f"{folder}/{NAME}"
            place(installer, ROOT / relative)
            result = {"target": "staging", "url": f"{URL}/{relative}", "sha256": manifest["sha256"]}
            if package is not None:
                place(package, ROOT / folder / CLOUD_NAME)
                result["cloud"] = {"url": f"{URL}/{folder}/{CLOUD_NAME}", "sha256": manifest["cloud"]["sha256"]}
            prune_staging()
            print(json.dumps(result))
            return
        folder = f"releases/{version}/{source}"
        relative = f"{folder}/{NAME}"
        files = [(installer, ROOT / relative, manifest["sha256"])]
        if package is not None:
            files.append((package, ROOT / folder / CLOUD_NAME, manifest["cloud"]["sha256"]))
        for _, release, sha256 in files:
            if release.exists() and hashlib.sha256(release.read_bytes()).hexdigest() != sha256:
                fail("This release path already holds different bytes")
        for source_file, release, _ in files:
            if not release.exists():
                place(source_file, release)
        latest_file = ROOT / "latest.json"
        if latest_file.exists():
            current = json.loads(latest_file.read_text()).get("version") or "0.0.0"
            if version_tuple(version) < version_tuple(current):
                fail(f"Refusing to go back from {current} to {version}")
        place(installer, ROOT / NAME)
        place(installer, ROOT / LEGACY_NAME)
        latest = {"version": version, "url": f"{URL}/{relative}?v={source}", "sha256": manifest["sha256"],
                  "size": manifest["size"], "source_sha": source,
                  "published_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        if package is not None:
            latest["cloud"] = {"version": version, "url": f"{URL}/{folder}/{CLOUD_NAME}?v={source}",
                               "sha256": manifest["cloud"]["sha256"], "size": manifest["cloud"]["size"]}
        write_json(latest_file, latest)
        print(json.dumps({"target": "prod", **latest}))


if __name__ == "__main__":
    main()
