#!/usr/bin/env python3
"""扫码共用件契约（CI 跑，不需要站点）：真 Chrome + 假摄像头放测试条码，用仓库里**当前**的共用读码器
（apps/pearnly_core/pearnly_core/public/scan/reader.js + vendor 解码器）必须看到画面、读出 TEST_CODE。

    python3 scripts/smoke/selftest.py        # Chrome 路径：SMOKE_CHROME（CI 里是 /usr/bin/google-chrome）

改坏读码器（不挂画面、解码器路径不对、结果回调丢了）这里就失败；各入口的整页流程由 run.py 在实验栈测。
"""
import functools
import http.server
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
spec = importlib.util.spec_from_file_location("make_barcode_video", HERE / "make_barcode_video.py")
barcode = importlib.util.module_from_spec(spec)
spec.loader.exec_module(barcode)
reaper_spec = importlib.util.spec_from_file_location("chrome_reaper", HERE / "chrome_reaper.py")
reaper = importlib.util.module_from_spec(reaper_spec)
reaper_spec.loader.exec_module(reaper)


def main() -> int:
    reaper.reap_orphans()   # 以前被打断留下的孤儿 Chrome 与旧临时目录
    work = Path(tempfile.mkdtemp(prefix="scan-selftest-"))
    (work / "assets").mkdir()
    (work / "assets" / "pearnly_core").symlink_to(ROOT / "apps" / "pearnly_core" / "pearnly_core" / "public")
    (work / "index.html").write_text('<!doctype html><html><body><script src="/assets/pearnly_core/scan/reader.js"></script></body></html>')
    video = work / "code.y4m"
    barcode.write(str(video))
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(work))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        job = {"url": f"http://localhost:{server.server_port}/index.html", "probe": "selftest", "video": str(video), "wait_ms": 2000,
               "args": {"code": barcode.TEST_CODE}}
        try:
            # 同 run.py：驱动整组跑，超时 / 被打断连 Chrome 一起收掉（chrome_reaper）
            _code, stdout, _stderr = reaper.run_driver(["node", str(HERE / "drive.mjs"), json.dumps(job)], timeout=150)
            raw = json.loads(stdout.strip().splitlines()[-1])
        except (subprocess.TimeoutExpired, ValueError, IndexError) as error:
            raw = {"result": None, "error": f"driver: {type(error).__name__}"}
    finally:
        server.shutdown()
        __import__("shutil").rmtree(work, ignore_errors=True)   # 测试页与条码视频的临时目录
    result = raw.get("result") or {}
    print(json.dumps(raw, ensure_ascii=False))
    if raw.get("error") or not result.get("ok"):
        print("扫码共用件契约失败：假摄像头的测试条码没被当前读码器读出（或没有画面）", file=sys.stderr)
        return 1
    print(f"扫码共用件契约通过：画面 {result['picture']['w']}x{result['picture']['h']}，读出 {result['code']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
