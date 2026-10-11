#!/usr/bin/env python3
"""Fail the activation when the server's deploy tooling is not this image's copy.

为什么要有它（2026-09-20 实际踩到）：一次发布其实是两半 —— 镜像是**按产品组**激活的，
而 `/opt/pearnly/deploy` 下那套宿主脚本（开户步骤、compose 定义、容量检查…）是**另一份拷贝**。
两份不同源时当时不会报错：站点在跑新代码、开户还在用旧脚本（或者反过来），
要等下一次开户或某次 `compose up -d` 才以很奇怪的方式暴露出来。

所以把"两份同源"变成激活的前置条件：只比部署拷贝真正包含的目录
（`infrastructure/production/`、`infrastructure/keycloak/themes/` 与 `scripts/production/`），逐文件比内容和可执行位，
缺文件、内容不同、多出不属于这一版的文件都直接失败，并打印差异。

用法：check-deploy-image.py <image> <deploy-root>
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# 登录主题：生产 compose 从部署拷贝挂给 Keycloak（2026-10-11 起随镜像带来，之前靠手工上传，pearnly-ai 一直缺）
PAYLOAD = ("infrastructure/production", "infrastructure/keycloak/themes", "scripts/production")
CONTAINER = "pearnly-deploy-check"


def _junk(relative: str) -> bool:
    parts = Path(relative).parts
    return Path(relative).name.startswith("._") or "__pycache__" in parts or Path(relative).name == ".DS_Store"


def _payload(root: Path, relative: str) -> dict:
    base = root / relative
    if not base.is_dir():
        return {}
    return {path.relative_to(root).as_posix(): (path.read_bytes(), os.access(path, os.X_OK))
            for path in base.rglob("*")
            if path.is_file() and not _junk(path.relative_to(root).as_posix())}


def _copy_out(image: str, target: Path) -> str:
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    created = subprocess.run(["docker", "create", "--name", CONTAINER, image], capture_output=True, text=True)
    if created.returncode:
        return created.stderr.strip() or "docker create failed"
    try:
        copied = subprocess.run(["docker", "cp", f"{CONTAINER}:/opt/pearnly/deploy-src/.", str(target)],
                                capture_output=True, text=True)
        if copied.returncode:
            return copied.stderr.strip() or "docker cp failed"
    finally:
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    return ""


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    image, root = sys.argv[1], Path(sys.argv[2])
    with tempfile.TemporaryDirectory(prefix="pearnly-deploy-") as folder:
        error = _copy_out(image, Path(folder))
        if error:
            print("Could not read the tooling copy out of the image: " + error, file=sys.stderr)
            return 2
        problems = []
        for relative in PAYLOAD:
            wanted, present = _payload(Path(folder), relative), _payload(root, relative)
            for path, (content, executable) in sorted(wanted.items()):
                if path not in present:
                    problems.append(f"missing on the server: {path}")
                    continue
                have, have_executable = present[path]
                if have != content:
                    problems.append(f"different content: {path}")
                elif have_executable != executable:
                    problems.append(f"different executable bit: {path}")
            for path in sorted(set(present) - set(wanted)):
                problems.append(f"not part of this release: {path}")
    if problems:
        print("The server deploy tooling is not the copy that ships with this image:", file=sys.stderr)
        for line in problems[:20]:
            print("  - " + line, file=sys.stderr)
        print("Sync it from this image first, then activate again:", file=sys.stderr)
        print(f"  docker rm -f {CONTAINER}; docker create --name {CONTAINER} {image}", file=sys.stderr)
        print(f"  docker cp {CONTAINER}:/opt/pearnly/deploy-src/. {root}/", file=sys.stderr)
        print(f"  docker rm -f {CONTAINER}", file=sys.stderr)
        return 1
    print("Deploy tooling matches this image")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
