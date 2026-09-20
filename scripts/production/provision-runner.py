#!/usr/bin/env python3
"""Mac control queue -> restricted cloud provisioning agent, serial and resumable."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from target import load_target, ssh_command

CONFIG = Path.home() / ".config/pearnly-production"
SSH = ssh_command("provisioner") + ["provision"]


def control(action, **args):
    result = subprocess.run(["docker", "--context", "colima-pearnly", "exec", "-i",
        "pearnly-sample-control-bench-1", "/home/frappe/frappe-bench/env/bin/python", "-m",
        "pearnly_control.runner_bridge"], input=json.dumps({"action": action, "args": args}),
        text=True, capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("Local control bridge unavailable")
    return json.loads(result.stdout)


def once():
    jobs = control("next", target=load_target()["target_name"])
    if not jobs:
        return "idle"
    job = jobs[0]
    key, action, p = job["step_key"], job["action"], job["payload"]
    control("report", step_key=key, status="running")
    try:
        if action == "purge_control":
            control("purge", request=p["request"])
            result = {"status": "done"}
        else:
            remote = dict(job)
            if action == "mark_active":
                remote["action"] = "verify_site"
            response = subprocess.run(SSH, input=json.dumps(remote), text=True, capture_output=True, timeout=2400)
            try:
                result = json.loads(response.stdout)
            except ValueError:
                raise RuntimeError("Cloud provisioning connection failed") from None
            if response.returncode or result.get("status") == "failed":
                raise RuntimeError(result.get("error", "Cloud provisioning failed"))
            if action == "mark_active":
                control("active", deployment=p["deployment"])
        control("report", step_key=key, status=result["status"], output="Cloud step verified")
        print(key, result["status"], flush=True)
    except Exception as error:
        control("report", step_key=key, status="failed", output=str(error)[:500])
        print(key, "failed:", str(error)[:500], flush=True)
        return "failed"
    return "done"


if __name__ == "__main__":
    os.umask(0o077)
    with (CONFIG / "provision-runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another cloud runner is active")
        if "--once" in sys.argv:
            once()
        else:
            while True:
                try:
                    outcome = once()
                except Exception as error:
                    print(str(error), flush=True)
                    outcome = "failed"
                # 一轮开通有十几步，步与步之间没有理由空转：做完就立刻拿下一步。
                # 失败退让 1 秒，队列空的时候才睡 5 秒。
                if outcome != "done":
                    time.sleep(1 if outcome == "failed" else 5)
