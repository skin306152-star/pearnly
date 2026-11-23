#!/usr/bin/env python3
"""本机 Mac 实验栈接 / 拆真模型（L3 核心集里要真模型的那几条用：CC 对话、Cowork 放票真识别）。

    python3 scripts/acceptance/maclab_model.py on  erp|cowork     # 接上：对话密钥 + 识别走 Claude（本进程直连）
    python3 scripts/acceptance/maclab_model.py off erp|cowork     # 拆掉并回读：两处都没有密钥、识别配置去掉
    python3 scripts/acceptance/maclab_model.py status erp|cowork  # 只看接没接（只报有没有、尾号，不报值）

- 栈由 `scripts/maclab.sh` 的同一组环境变量定（MACLAB_STACK、MACLAB_CONFIG_DIR、端口……），与运行器一致。
- 密钥只从本机 `~/.config/pearnly-claude-ocr/runtime.env` 的 PEARNLY_ANTHROPIC_API_KEY 取（用户 11-10 定：测试一律 6QAA 那把），
  写两处：栈的运行参数文件（600，进容器环境 → `claude_ocr.client` 没有远端地址时本进程直连）与站点配置 `pearnly_assistant_api_key`
  （CC 对话）。经标准输入交给容器，不进命令行、不打印；回读只比尾号。
- 识别准入与「已配置」的站点项沿用 `scripts/scenarios/real_run_preflight.py` 的 OCR_CONFIG（合成值，只为过判断），引擎 claude。
- 改了运行参数文件要重建容器：`on` / `off` 末尾自动 `maclab.sh up <产品>`（同一份栈，只重建变了的容器）。
- 真模型费用由场景的 budget_usd 与运行器到额即停管；本脚本不花钱。跑完一定 `off`（核心集记录里的收尾一步）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEY_FILE = Path(os.environ.get("PEARNLY_ACC_MODEL_KEYS", Path.home() / ".config" / "pearnly-claude-ocr" / "runtime.env"))
KEY_NAME = "PEARNLY_ANTHROPIC_API_KEY"
SITE_KEY = "pearnly_assistant_api_key"
MARK = "# maclab_model.py on（跑完 off 删掉）"


def preflight():
    sys.path.insert(0, str(ROOT / "scripts" / "scenarios"))
    import real_run_preflight  # noqa: E402

    return real_run_preflight.OCR_CONFIG, real_run_preflight.SHARED_CONFIG


def stack(app: str) -> dict:
    prefix = os.environ.get("MACLAB_STACK") or "pearnly-maclab"
    config = Path(os.environ.get("MACLAB_CONFIG_DIR") or Path.home() / ".config" / "pearnly-maclab")
    return {"env": config / f"{app}.env", "bench": f"{prefix}-{app}-bench-1", "site": f"{app}.maclab.localhost"}


def read_key() -> str:
    for line in KEY_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith(KEY_NAME + "="):
            key = line.split("=", 1)[1].strip()
            if key.startswith("sk-ant-"):
                return key
    raise SystemExit(f"{KEY_FILE} 里没有像样的 {KEY_NAME}")


def tail(value: str) -> str:
    return value[-4:] if value else ""


def env_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith(KEY_NAME + "=") and line != MARK]


def write_env(path: Path, key: str | None) -> None:
    lines = env_lines(path) + ([MARK, f"{KEY_NAME}={key}"] if key else [])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


# 容器里改站点配置：值从标准输入来（不进命令行），只回键名与尾号
EDIT = r'''
import json, sys
spec = json.load(sys.stdin)
path = "/home/frappe/frappe-bench/sites/" + spec["site"] + "/site_config.json"
conf = json.load(open(path))
conf.update(spec["set"])
for k in spec["drop"]:
    conf.pop(k, None)
open(path, "w").write(json.dumps(conf, indent=1, sort_keys=True))
print(json.dumps({"has_key": bool(conf.get("pearnly_assistant_api_key")), "tail": str(conf.get("pearnly_assistant_api_key") or "")[-4:],
                  "engine": conf.get("pearnly_ocr_engine")}))
'''


def site_config(where: dict, set_: dict, drop: list) -> dict:
    done = subprocess.run(["docker", "exec", "-i", where["bench"], "python3", "-c", EDIT], input=json.dumps(
        {"site": where["site"], "set": set_, "drop": drop}), capture_output=True, text=True, timeout=60)
    if done.returncode:
        raise SystemExit(f"改站点配置失败：{done.stderr[-300:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def redis_of(where: dict) -> str:
    done = subprocess.run(["docker", "exec", where["bench"], "python3", "-c",
                           "import json;print(json.load(open('/home/frappe/frappe-bench/sites/common_site_config.json')).get('redis_queue',''))"],
                          capture_output=True, text=True, timeout=60)
    return done.stdout.strip()


def container_has_key(where: dict) -> str:
    done = subprocess.run(["docker", "exec", where["bench"], "sh", "-c", f'printf %s "${KEY_NAME}" | tail -c 4'],
                          capture_output=True, text=True, timeout=60)
    return done.stdout.strip()


def up(app: str) -> None:
    done = subprocess.run(["bash", str(ROOT / "scripts" / "maclab.sh"), "up", app], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, errors="replace", timeout=1200)  # 它的输出夹着截断的中文：按替换解码
    if done.returncode:
        raise SystemExit(f"maclab.sh up {app} 失败：{(done.stderr or done.stdout)[-600:]}")


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] not in ("on", "off", "status") or args[1] not in ("erp", "cowork"):
        print(__doc__)
        return 2
    action, app = args
    where = stack(app)
    if not where["env"].is_file():
        raise SystemExit(f"{where['env']} 不存在：先 maclab.sh up {app}")
    ocr, shared = preflight()
    if action == "on":
        key = read_key()
        write_env(where["env"], key)
        up(app)
        values = {**ocr[app], "pearnly_ocr_engine": "claude", "pearnly_ocr_budget_redis": redis_of(where), SITE_KEY: key}
        conf = site_config(where, values, [])
        ok = conf["has_key"] and conf["tail"] == tail(key) and container_has_key(where) == tail(key) and conf["engine"] == "claude"
    elif action == "off":
        write_env(where["env"], None)
        up(app)
        conf = site_config(where, {}, [SITE_KEY, *ocr[app], *shared])
        ok = not conf["has_key"] and not container_has_key(where) and not conf["engine"]
    else:
        conf = site_config(where, {}, [])
        ok = True
    print(json.dumps({"app": app, "stack": where["bench"].rsplit("-bench-1", 1)[0], "site_key": conf["has_key"], "site_key_tail": conf["tail"],
                      "container_key_tail": container_has_key(where), "ocr_engine": conf["engine"], "ok": ok}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
