"""只在测试环境用：发票每份在服务器上的 CPU 与 SQL 条数（不付费，2026-10-01 发票 CPU 优化的测量方法）。

上传 → 提交 → 识别任务 → 存结果 → 扣费，全走真代码；只把 Cloud Run 的回答换成本站一份已有的单页照片识别结果、扣费直接回成功
（不碰账本）、不往识别队列投递。前 5 份预热不计入，之后按 CPU 时间（time.process_time）做 cProfile，并按调用位置数 SQL。

副作用：在指定站点上真的建原件、识别任务单与票（标题 / 参考号带 tag），用作压测数据；不调谷歌、不扣费。
**识别进程必须先停**（否则投递的识别任务会被真的执行）。用法（测试栈 cowork web 容器内）：
    python testenv-invoice-cpu.py <site> <user> <N> <tag> [top]
详见 docs/work/2026-10-01-invoice-cpu.md。
"""
import cProfile
import io
import json
import pstats
import random
import sys
import time
from collections import defaultdict
from unittest import mock

import frappe

SITE, USER, N, TAG = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
TOP = int(sys.argv[5]) if len(sys.argv) > 5 else 25
WARM = 5
frappe.init(site=SITE, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
frappe.set_user(USER)
frappe.local.lang = "zh"

from PIL import Image  # noqa: E402
from pearnly_core.execution import work as core_work  # noqa: E402
from pearnly_cowork.access import scope  # noqa: E402
from pearnly_cowork.files import service as files  # noqa: E402
from pearnly_cowork.ocr.execution import access, service, workflow  # noqa: E402
from pearnly_cowork.ocr.remote import forward  # noqa: E402

# 一份已有的单页照片识别结果（有发票、已存成票）当作 Cloud Run 的回答
canned = frappe.db.sql(f"""select b.result_json, b.provider_evidence from `tab{access.DOCTYPE}` b
    join `tabCowork Source File` s on s.name = b.source_file
    where b.phase='saved' and b.page_count=1 and s.kind='image' and b.result_json like '%%invoice%%'
    order by b.creation desc limit 1""")
if not canned:
    raise SystemExit("no canned single-page result found")
pipeline_json, evidence = canned[0][0], json.loads(canned[0][1] or "[]")
workspace = scope.default_workspace()

counts = defaultdict(int)
real_sql = frappe.db.sql
current = {"phase": None}


import traceback
sites = defaultdict(int)


def counting_sql(*args, **kwargs):
    if current["phase"]:
        counts[current["phase"]] += 1
        frames = [f for f in traceback.extract_stack(limit=40) if "/apps/pearnly_" in f.filename]
        if frames:
            f = frames[-1]
            outer = frames[-2] if len(frames) > 1 else None
            sites[(current["phase"], f"{f.filename.split('/apps/')[-1]}:{f.lineno} {f.name}"
                   + (f" <- {outer.name}" if outer else ""))] += 1
    return real_sql(*args, **kwargs)


profiles = {phase: cProfile.Profile(timer=time.process_time) for phase in ("upload", "submit", "run")}
cpu = defaultdict(float)
rng = random.Random(TAG)


def jpg():
    image = Image.new("RGB", (600, 800), tuple(rng.randrange(256) for _ in range(3)))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=70)
    return buffer.getvalue()


def phase(name, fn):
    current["phase"] = name
    started = time.process_time()
    profiles[name].enable()
    try:
        return fn()
    finally:
        profiles[name].disable()
        cpu[name] += time.process_time() - started
        current["phase"] = None


done = 0
with mock.patch.object(frappe.db, "sql", side_effect=counting_sql), \
        mock.patch.object(forward, "settings", return_value={"url": "x"}), \
        mock.patch.object(forward, "recognize", return_value=(pipeline_json, evidence)), \
        mock.patch.object(service.billing_client, "charge",
                          return_value={"ok": True, "charged_thb": 0.0, "kind": "pdf", "units": 1, "tier": "B"}), \
        mock.patch.object(core_work, "kick", return_value=True):
    for index in range(N + WARM):
        if index == WARM:  # 预热的几份不计入（元数据、角色等缓存热起来，与长期运行的进程一致）
            cpu.clear(); counts.clear(); sites.clear()
            profiles.update({phase: cProfile.Profile(timer=time.process_time) for phase in ("upload", "submit", "run")})
            done = 0
        key = f"{TAG}-{index}"
        content = jpg()
        source = phase("upload", lambda: files.create(workspace, f"{key}.jpg", content, key))
        frappe.db.commit()
        batch = phase("submit", lambda: service.submit(source["name"], key))
        frappe.db.commit()
        work_name = frappe.db.get_value(access.DOCTYPE, batch["name"], "work_name")
        phase("run", lambda: core_work.run(work_name))
        frappe.db.commit()
        state = frappe.db.get_value(access.DOCTYPE, batch["name"], ["phase", "billing_state"], as_dict=True)
        done += state.phase == "saved" and state.billing_state == "charged"

print(f"== {TAG}: {done}/{N} saved+charged")
for name in ("upload", "submit", "run"):
    print(f"  {name}: cpu {cpu[name] / N:.3f}s/份  sql {counts[name] / N:.1f}/份")
print(f"  total cpu {sum(cpu.values()) / N:.3f}s/份  sql {sum(counts.values()) / N:.1f}/份")
print("--- sql by call site (per invoice)")
for (name, site), count in sorted(sites.items(), key=lambda kv: -kv[1])[:45]:
    print(f"   {name:6} {count / N:5.1f}  {site[:150]}")
for name in ("run", "submit", "upload"):
    out = io.StringIO()
    stats = pstats.Stats(profiles[name], stream=out)
    stats.sort_stats("cumulative").print_stats(TOP)
    lines = [line for line in out.getvalue().splitlines() if "/apps/" in line or "site-packages" in line or "ncalls" in line]
    print(f"--- {name} top cumulative (cpu s, per {N})")
    for line in lines[:TOP]:
        print("   ", line.replace("/home/frappe/frappe-bench/", "")[:200])
frappe.destroy()
