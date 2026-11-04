#!/usr/bin/env python3
"""吞错闸门自测：扫码的 `PearnlyScan.report(...)` 只在它的定义原样是「预期原因跳过、其余交 pearnlyCaught」时算上报（主会话 11-04）。

- 仓库里的定义正常：扫码 4 处 catch（desk/scan.js 1 处、scan/reader.js 3 处）算上报，闸门通过。
- 定义被改成什么都不做 / 只跳过不上报 / 换个函数导出：这 4 处不算上报，闸门失败并点名定义。
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import shutil
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_swallowed_errors", REPO / "scripts" / "check-swallowed-errors.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

READER = gate.SCAN_READER
DESK = "apps/pearnly_core/pearnly_core/public/desk/scan.js"
DEFINITION = ("\tfunction report(error, kind) {\n\t\tif (EXPECTED.has(reason(error))) return;\n"
              "\t\tglobalThis.pearnlyCaught?.(error, kind);\n\t}\n")


def scan_catches():
    return [i for i in gate.scan() if i["file"] in (READER, DESK) and gate.SCAN_REPORT.search(gate.mask(i["handler"]))]


def run_main():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = gate.main([])
    return code, out.getvalue()


# 1. 仓库原样：定义核对通过，4 处算上报，整个闸门通过
assert gate.scan_report_ok(), "PearnlyScan.report definition in the repository should pass"
found = scan_catches()
assert len(found) == 4, found
assert all(i["kind"] == "report" for i in found), found
assert sum(i["file"] == DESK for i in found) == 1 and sum(i["file"] == READER for i in found) == 3, found
code, out = run_main()
assert code == 0, out

# 2. 定义被改坏：在临时副本里改 reader.js，闸门必须失败并点名定义
source = (REPO / READER).read_text(encoding="utf-8")
assert source.count(DEFINITION) == 1, "the test expects the report definition written exactly as in reader.js"
broken = {
    "does nothing": source.replace(DEFINITION, "\tfunction report(error, kind) {}\n"),
    "skips without reporting": source.replace(DEFINITION, "\tfunction report(error, kind) {\n\t\tif (EXPECTED.has(reason(error))) return;\n\t}\n"),
    "returns before reporting": source.replace(DEFINITION, "\tfunction report(error, kind) {\n\t\treturn;\n\t\tglobalThis.pearnlyCaught?.(error, kind);\n\t}\n"),
    "exports another function": source.replace("window.PearnlyScan = { formats: WANTED, kind: kind_of, reason, report,",
                                               "window.PearnlyScan = { formats: WANTED, kind: kind_of, reason, report: () => {},"),
}
assert broken["exports another function"] != source
real_root, real_registry = gate.ROOT, gate.REGISTRY
for case, text in broken.items():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        for rel in (READER, DESK, "tests/baseline/swallowed_errors.toml"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(REPO / rel, root / rel)
        (root / READER).write_text(text, encoding="utf-8")
        gate.ROOT, gate.REGISTRY = root, root / "tests" / "baseline" / "swallowed_errors.toml"
        try:
            assert not gate.scan_report_ok(), case
            found = scan_catches()
            assert len(found) == 4 and not any(i["kind"] == "report" for i in found), (case, found)
            code, out = run_main()
            assert code == 1, (case, out)
            assert "PearnlyScan.report 的定义" in out and "4 处扫码 catch 不算上报" in out, (case, out)
        finally:
            gate.ROOT, gate.REGISTRY = real_root, real_registry

# 3. 只认这一个名字：别的叫 report 的函数不算上报
assert gate.classify('(error) => report(error, "x")', True) != "report"
assert gate.classify('(error) => helper.report(error, "x")', True) != "report"
assert gate.classify('(error) => window.PearnlyScan.report(error, "x")', False) != "report"
print("swallowed error gate: PearnlyScan.report counts as reporting only while its definition reports everything but the expected camera conditions")
