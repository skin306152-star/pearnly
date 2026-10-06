"""DMS-only bench configuration (manual-read queue, scheduler tick, menu-6 tutorial videos); no Site or external IO.

Run explicitly against the DMS bench common_site_config.json before restarting
its scheduler/workers. Other product benches must not invoke this operation.
"""
import argparse
import copy
import json
from pathlib import Path
from urllib.parse import quote

# LINE 菜单第 6 项「คู่มือ」的三段教程视频（Cloudflare R2 `pearnly-media`，所有租户同一批文件）。
# 写进整台 DMS bench 共用的配置：现有和以后新开的 DMS 租户都直接能播；某个站点要换自己的视频，
# 在它自己的 site_config 里写同名键即可覆盖（`setdefault`，不改已有值）。2026-10-06 ris 漏配过一次。
MANUAL_MEDIA = "https://media.pearnly.com/"
MANUAL_VIDEOS = {
    "pearnly_dms_manual_setup": ("DMS-1-ตั้งค่าเชื่อมต่อและพนักงาน.mp4", "封面/6.0-ติดตั้งครั้งแรก.jpg"),
    "pearnly_dms_manual_customer": ("DMS-2-เมนู1-จัดทำข้อมูลลูกค้า.mp4", "封面/6.1-จัดทำข้อมูลลูกค้า.jpg"),
    "pearnly_dms_manual_booking": ("DMS-3-เมนู2-จัดทำใบจอง.mp4", "封面/6.2-จัดทำใบจอง.jpg"),
}


def configured(values):
    result = copy.deepcopy(values)
    result.setdefault("workers", {}).setdefault("dms_sync", {})["timeout"] = 420
    # ADR-013: daytime change sync runs every minute, so this DMS bench's
    # scheduler must look every 60 s (Frappe's default is 240 s). Other benches
    # keep their own cadence; this script only ever runs against the DMS bench.
    result["scheduler_tick_interval"] = 60
    for key, (video, preview) in MANUAL_VIDEOS.items():
        result.setdefault(key, {"video": MANUAL_MEDIA + quote(video), "preview": MANUAL_MEDIA + quote(preview)})
    return result


def install(path=None):
    target = Path(path or "/home/frappe/frappe-bench/sites/common_site_config.json")
    data = configured(json.loads(target.read_text()))
    temporary = target.with_suffix(".dms-runtime.tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.chmod(target.stat().st_mode & 0o777)
    temporary.replace(target)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path")
    args = parser.parse_args()
    install(args.path)
