#!/usr/bin/python3
"""Forced SSH command: stage only a pinned Pearnly image; never start services.

stdin is two lines: image digest, ephemeral GHCR read token. No shell command,
server credential, or broad registry token is accepted from the CI caller.
"""
import os
import re
import subprocess
import sys
import tempfile


def main():
    image = sys.stdin.readline(200).strip()
    token = sys.stdin.readline(4096).strip()
    if not re.fullmatch(r"ghcr\.io/skin306152-star/pearnly/frappe@sha256:[0-9a-f]{64}", image):
        raise SystemExit("Only a pinned Pearnly image is allowed")
    if not token or len(token) > 2048:
        raise SystemExit("Missing registry credential")
    with tempfile.TemporaryDirectory(prefix="pearnly-registry-") as directory:
        env = {**os.environ, "DOCKER_CONFIG": directory}
        subprocess.run(
            ["docker", "login", "ghcr.io", "-u", "skin306152-star", "--password-stdin"],
            input=token, text=True, env=env, check=True, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(["docker", "pull", image], env=env, check=True)
        subprocess.run(["docker", "image", "inspect", image, "--format",
                        '{{index .Config.Labels "org.opencontainers.image.revision"}}'],
                       env=env, check=True)


if __name__ == "__main__":
    main()
