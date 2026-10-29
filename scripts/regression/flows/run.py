#!/usr/bin/env python3
"""跑业务流程用例：每个用例一个 JSON（`cases/*.json`），里面的 `${...}` 用 `seed.py` 的输出替换，按用例要的账号开会话，
交给 `steps.mjs` 在无头 Chrome 里一步步走，汇总「通过 / 不通过」。本机跑，实验室在 colima-pearnly 里。

    python3 scripts/regression/flows/run.py --seed out/seed-new.json --seed out/seed-old.json cases/stock_in_scan.json …
    环境：FLOWS_CONTAINER（默认 pearnly-flows-lab-bench-1）、FLOWS_PORT（默认 18791）、FLOWS_VIDEOS（假摄像头视频目录）、
          FLOWS_OUT（截图与结果目录，默认 out/flows）

用例格式：{"name", "datasets": ["new", "old"], "user": "admin", "start": "/desk/…", "mobile": false, "video": "qr-qr.y4m",
          "steps": [{"name", "js" | "file": "js/xx.js", "wait_ms", "start", "shot", "exec": ["<labctl 动作>", …]}]}。占位：${site}、${company}、${products.qr}、${codes.qr}、
          ${warehouses.<仓名>}（任意键路径）。`variants`：[{"suffix", "video", "vars": {…}}]，同一流程每个变体跑一遍，
          变体里的值用 ${v.<键>} 取（例如每种码一个变体：各自的假摄像头视频和该认出的商品）。
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
CONTAINER = os.environ.get("FLOWS_CONTAINER", "pearnly-flows-lab-bench-1")
PORT = os.environ.get("FLOWS_PORT", "18791")
SITES = {"new": "erp-new.flows.localhost", "old": "erp-old.flows.localhost"}


def sid(site, user):
    email = user if "@" in user else f"{user}@{site.split('.')[0]}.flows"
    out = subprocess.run(["docker", "--context", "colima-pearnly", "exec", "-w", "/home/frappe/frappe-bench/sites", CONTAINER,
                          "../env/bin/python", "/workspace/scripts/regression/flows/sid.py", site, email],
                         capture_output=True, text=True, stdin=subprocess.DEVNULL).stdout
    found = re.search(r"^SID (\S+)", out, re.M)
    if not found:
        raise SystemExit(f"no session for {email} on {site}")
    return found.group(1)


def lookup(data, path):
    path = path[5:] if path.startswith("seed.") else path
    for part in path.split("."):
        data = data[part] if isinstance(data, dict) else data[int(part)]
    return data


def fill(text, seed):
    def value(match):
        got = lookup(seed, match.group(1))
        return json.dumps(got, ensure_ascii=False)[1:-1] if isinstance(got, str) else json.dumps(got, ensure_ascii=False)
    return re.sub(r"\$\{([^}]+)\}", value, text)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", action="append", default=[])
    parser.add_argument("cases", nargs="+")
    args = parser.parse_args()
    seeds = {}
    for path in args.seed:
        data = json.loads(Path(path).read_text())
        seeds[data["dataset"]] = data
    out = Path(os.environ.get("FLOWS_OUT", "out/flows"))
    videos = Path(os.environ.get("FLOWS_VIDEOS", "videos"))
    env = {**os.environ, "FLOWS_CHROME_RULES": f"MAP *.flows.localhost 127.0.0.1:{PORT}"}
    summary = []
    for case_path in args.cases:
        raw = Path(case_path).read_text()
        case = json.loads(raw)
        for dataset, variant in [(d, v) for d in case.get("datasets") or ["new"] for v in case.get("variants") or [{}]]:
            if dataset not in seeds:
                continue
            site = SITES[dataset]
            seed = {**seeds[dataset], "site": site}
            vars_ = {key: fill(value, seed) if isinstance(value, str) else value for key, value in (variant.get("vars") or {}).items()}
            job = json.loads(fill(raw, {**seed, "v": vars_}))
            job["video"] = variant.get("video", job.get("video"))
            for step in job["steps"]:
                if step.get("file"):  # 步骤的页面脚本放在 cases/js/ 下，同样替换占位；给列表时按顺序拼起来（公共函数放前面）
                    files = step["file"] if isinstance(step["file"], list) else [step["file"]]
                    step["js"] = fill("\n".join((Path(case_path).parent / f).read_text() for f in files), {**seed, "v": vars_})
                if step.get("exec"):  # 步骤前在实验室容器里跑 labctl.py 的一个动作（页面做不到的模拟，见 labctl.py）
                    step["exec"] = ["docker", "--context", "colima-pearnly", "exec", "-w", "/home/frappe/frappe-bench/sites", CONTAINER,
                                    "../env/bin/python", "/workspace/scripts/regression/flows/labctl.py", site, *step["exec"]]
            name = f"{job['name']}{('-' + variant['suffix']) if variant.get('suffix') else ''}@{dataset}"
            spec = {"base": f"http://{site}", "cookie_domain": site, "sid": sid(site, job.get("user", "admin")),
                    "start": job.get("start", "/desk"), "mobile": job.get("mobile", False),
                    "video": str((videos / job["video"]).resolve()) if job.get("video") else None,
                    "shots": str(out / name), "steps": job["steps"]}
            (out / name).mkdir(parents=True, exist_ok=True)
            spec_path = out / name / "job.json"
            spec_path.write_text(json.dumps(spec, ensure_ascii=False))
            result = subprocess.run(["node", str(HERE / "steps.mjs"), str(spec_path)], capture_output=True, text=True, env=env,
                                    timeout=900)
            rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
            (out / name / "result.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows))
            passed = bool(rows) and all(r.get("ok") for r in rows)
            summary.append({"case": name, "ok": passed, "failed": [r.get("step") or r.get("error") for r in rows if not r.get("ok")]})
            print(("PASS " if passed else "FAIL ") + name, flush=True)
            for row in rows:
                if not row.get("ok"):
                    print("   ✗", row.get("step") or "", json.dumps(row.get("value"), ensure_ascii=False)[:600], row.get("error") or "",
                          (row.get("page_errors") or [])[:3], flush=True)
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    sys.exit(0 if all(row["ok"] for row in summary) else 1)


if __name__ == "__main__":
    main()
