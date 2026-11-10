"""上传大小各层对齐（11-10 压测：CC 说一份最多 30MB，27.7MB 的 50 页扫描 PDF 在约 25MB 处被回 413，页面只说「出错了」）。

一份文件从浏览器到站点要过好几层，每层各有上限，最紧的那层说了算：
- Cloudflare 免费版 100MB（不在本仓）；
- 宿主机 nginx（开户写的租户站配置，`provision-agent.nginx`，部署时 `refresh-tenant-nginx.py` 按模板重写）；
- 容器 nginx（镜像默认 `CLIENT_MAX_BODY_SIZE=50m`）；
- Frappe：除它自己的 `upload_file` 外，**整个请求体**按 bench 配置 `max_file_size` 收，没配就是 25MB
  （`frappe/app.py` 的 `init_request`，在任何钩子之前就解析表单，所以只能靠配置，不能在接口里放宽）；
- 接口自己：一份文件最多 `originals.rules.UPLOAD_MAX_BYTES`。
请求体比文件多一层 multipart 外壳（分隔行、字段名、文件名），所以 Frappe 与 nginx 按 `UPLOAD_REQUEST_MAX_BYTES` 收。
放不进共用层的几处（零依赖的 CC 契约、前端、宿主机脚本）各写一份数字，这里逐个核对等于共用层那一个。
超限时页面照实说「文件超过 30 MB」（四语词条 `upload.tooLarge`），浏览器先比一次大小、不白传 30 秒。
"""
import importlib.util
import json
import pathlib
import re
import shutil
import sys
import tempfile
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"
ERP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
FRONTEND = ROOT / "frontend/assistant"
MIB = 1024 * 1024
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rules = _load("upload_limits_rules", CORE / "originals/rules.py")


def _multipart_overhead(fields: dict, filename: str) -> int:
    """浏览器 FormData 的写法（Chrome：`----WebKitFormBoundary` + 16 位）除去文件字节以外的长度。"""
    boundary = "----WebKitFormBoundary" + "A" * 16
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n' for key, value in fields.items()]
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 "Content-Type: application/pdf\r\n\r\n")
    parts.append(f"\r\n--{boundary}--\r\n")
    return len("".join(parts).encode("utf-8"))


class SharedLimitTests(unittest.TestCase):
    def test_one_limit_for_a_file_people_hand_in(self):
        self.assertEqual(rules.UPLOAD_MAX_BYTES, 30 * MIB)
        contracts = _load("upload_limits_contracts", CORE / "assistant/contracts.py")
        self.assertEqual(contracts.UPLOAD_MAX_BYTES, rules.UPLOAD_MAX_BYTES)
        # 代录资料区与 ERP 对话收单直接用共用层那一个（ERP 原来 20MB：对话里收下的 20–30MB 文件交识别时被拒）
        for path, name in ((CORE / "express_operator/api.py", "UPLOAD_MAX"), (ERP / "integrations/intake.py", "MAX_BYTES")):
            text = path.read_text(encoding="utf-8")
            self.assertRegex(text, r"from pearnly_core\.originals\.rules import [^\n]*\bUPLOAD_MAX_BYTES\b", path.name)
            self.assertRegex(text, rf"(?m)^{name} = UPLOAD_MAX_BYTES$", path.name)
        # 页面的数由服务端写进来（同一个来源），前端不另写一份
        page = (CORE / "www/assistant.py").read_text(encoding="utf-8")
        self.assertIn("from pearnly_core.originals.rules import UPLOAD_MAX_BYTES", page)
        self.assertIn("context.upload_max_bytes = UPLOAD_MAX_BYTES", page)
        self.assertIn("window.pearnly_upload_max_bytes = {{ upload_max_bytes | int }};",
                      (CORE / "www/assistant.html").read_text(encoding="utf-8"))
        script = (FRONTEND / "src/lib/frappe.ts").read_text(encoding="utf-8")
        self.assertIn("Number(window.pearnly_upload_max_bytes)", script)
        self.assertNotRegex(script, r"\b30 \* 1024 \* 1024\b", "前端不写死上限")

    def test_the_request_limit_leaves_room_for_the_multipart_envelope(self):
        # 最长的两种表单：对话附件（session_id + 文件）、代录资料区（公司 + 请求键 + 任务 + 文件）；文件名按 255 个泰文字算
        name = "ใ" * 251 + ".pdf"
        chat = _multipart_overhead({"session_id": "s" * 140}, name)
        operator = _multipart_overhead({"company": "c" * 140, "request_key": "upl_" + "0" * 32, "task": "t" * 140}, name)
        for overhead in (chat, operator):
            self.assertLessEqual(rules.UPLOAD_MAX_BYTES + overhead, rules.UPLOAD_REQUEST_MAX_BYTES)
        # 外壳只留够用的余量，不借它把上限悄悄放大
        self.assertLessEqual(rules.UPLOAD_REQUEST_MAX_BYTES - rules.UPLOAD_MAX_BYTES, 2 * MIB)
        self.assertEqual(rules.UPLOAD_REQUEST_MAX_BYTES % MIB, 0, "nginx 按整 MB 写")


class BenchConfigTests(unittest.TestCase):
    def test_frappe_takes_the_request_limit_from_the_bench_config(self):
        limits = _load("upload_limits_bench", CORE / "originals/upload_limits.py")
        before = {"db_host": "mariadb", "workers": {"ocr": {"timeout": 600}}}
        after = limits.bench_values(before)
        self.assertEqual(after["max_file_size"], rules.UPLOAD_REQUEST_MAX_BYTES)
        self.assertEqual({k: v for k, v in after.items() if k != "max_file_size"}, before)
        self.assertNotIn("max_file_size", before, "不改传进来的那份")
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp, "common_site_config.json")
            path.write_text(json.dumps(before))
            path.chmod(0o640)
            limits.install(path)
            limits.install(path)  # 幂等
            self.assertEqual(json.loads(path.read_text()), after)
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_every_bench_gets_the_request_limit(self):
        activate = (ROOT / "scripts/production/activate-product.sh").read_text(encoding="utf-8")
        line = '"${compose[@]}" run --rm --no-deps "$product-web" env/bin/python -m pearnly_core.originals.upload_limits'
        self.assertIn(line, activate, "生产三个产品激活时都写（不在按产品分的 if 里），迁移前、web 重启前")
        position = activate.index(line)
        self.assertNotIn("if [[", activate[activate.rindex("\n", 0, position):position])
        self.assertLess(position, activate.index('bench --site "$site" migrate'))
        self.assertLess(position, activate.index("up -d"))
        # 实验栈、测试机与线上同一个值
        for path in (ROOT / "scripts/regression/lab_init.py", ROOT / "infrastructure/testenv/finish-start.py"):
            self.assertIn("upload_limits.bench_values(", path.read_text(encoding="utf-8"), path.name)


class ProxyTests(unittest.TestCase):
    def test_the_proxies_in_front_let_the_largest_request_through(self):
        agent = _load("upload_limits_provision_agent", ROOT / "infrastructure/production/provision-agent.py")
        sizes = re.findall(r"client_max_body_size (\d+)m;", agent.nginx("acme.pearnly.com", "cowork", True))
        self.assertEqual(sizes, [str(rules.UPLOAD_REQUEST_MAX_BYTES // MIB)], "租户站与 Frappe 同一个请求体上限")
        # 容器 nginx 用镜像默认的 50m：编排里不得设得更小
        for compose in (ROOT / "infrastructure/production/compose.yml", ROOT / "infrastructure/compose/regression-lab.yml"):
            for value in re.findall(r"CLIENT_MAX_BODY_SIZE[=:]\s*\"?(\d+)m", compose.read_text(encoding="utf-8")):
                self.assertGreaterEqual(int(value) * MIB, rules.UPLOAD_REQUEST_MAX_BYTES, compose.name)
        # 原件交甲骨文存（base64 装进 JSON，约 4/3）：甲骨文服务收得下最大的一份
        server = (CORE / "claude_ocr/server.py").read_text(encoding="utf-8")
        limit = int(re.search(r"MAX_BODY = (\d+) \* 1024 \* 1024", server).group(1)) * MIB
        self.assertLess(-(-rules.UPLOAD_MAX_BYTES // 3) * 4 + 4096, limit)
        self.assertLessEqual(rules.UPLOAD_MAX_BYTES, rules.MAX_FILE_BYTES, "识别收得下的才让传")


class MessageTests(unittest.TestCase):
    def test_too_large_says_the_limit_in_four_languages(self):
        rows = [line.split("\t") for line in (CORE / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()]
        row = next((r for r in rows if r[-1] == "upload.tooLarge"), None)
        self.assertIsNotNone(row, "共用层词条 upload.tooLarge")
        en, zh, th, ja = row[:4]
        self.assertEqual(en, "File is larger than {0} MB")
        self.assertEqual(zh, "文件超过 {0} MB")
        for text in (th, ja):
            self.assertIn("{0} MB", text)
        frappe = types.ModuleType("frappe")
        frappe._ = lambda key: en if key == "upload.tooLarge" else key
        saved = sys.modules.get("frappe")
        sys.modules["frappe"] = frappe
        try:
            limits = _load("upload_limits_message", CORE / "originals/upload_limits.py")
            self.assertEqual(limits.too_large(), "File is larger than 30 MB")
        finally:
            if saved is None:
                sys.modules.pop("frappe", None)
            else:
                sys.modules["frappe"] = saved

    def test_the_upload_endpoints_say_too_large_instead_of_a_generic_refusal(self):
        upload = (CORE / "assistant/files.py").read_text(encoding="utf-8").split("def _upload(", 1)[1].split("\ndef ", 1)[0]
        check = ("if len(content) > contracts.UPLOAD_MAX_BYTES:\n        from pearnly_core.originals import upload_limits\n\n"
                 "        frappe.throw(upload_limits.too_large())")
        self.assertIn(check, upload)
        self.assertLess(upload.index(check), upload.index("contracts.upload_name"))
        intake = (CORE / "express_operator/api.py").read_text(encoding="utf-8").split("def intake(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if len(content) > UPLOAD_MAX:\n        frappe.throw(upload_limits.too_large())", intake)


NODE = shutil.which("node")
ESBUILD = FRONTEND / "node_modules/esbuild/lib/main.js"

PAGE = r"""
import "cc-test-setup";
import assert from "node:assert/strict";
import { createAttachmentAdapter } from "@/lib/managed-agents/attachments";
import { uploadMethod } from "@/lib/frappe";

const g = globalThis as any;
const UPLOAD_MAX_BYTES = 30 * 1024 * 1024;
g.pearnly_upload_max_bytes = UPLOAD_MAX_BYTES;  // 服务端写进页面的那个数
g.URL.createObjectURL = () => "blob:local";
const calls: string[] = [];
const run = async (file: File) => {
  const adapter = createAttachmentAdapter(async () => "s1");
  const steps: any[] = [];
  for await (const step of adapter.add!({ file }) as any) steps.push(step.status);
  return steps;
};
const big = (bytes: number) => {
  const file = new File([new Uint8Array(8)], "scan-50.pdf", { type: "application/pdf" });
  Object.defineProperty(file, "size", { value: bytes });
  return file;
};
"""


@unittest.skipUnless(NODE and ESBUILD.exists(), "需要 node 与前端依赖（npm ci --prefix frontend/assistant）")
class PageTests(unittest.TestCase):
    def run_page(self, body, lang="en"):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_assistant_ui import run_ts

        run_ts(PAGE + body, lang=lang)

    def test_a_file_over_the_limit_is_refused_on_the_page_with_the_limit(self):
        self.run_page(r"""
g.fetch = async (url: string) => { calls.push(String(url)); throw new Error("must not upload"); };
const steps = await run(big(UPLOAD_MAX_BYTES + 1));
assert.deepEqual(steps.map((s: any) => s.type), ["incomplete"]);
assert.equal(steps[0].message, "File is larger than 30 MB");
assert.deepEqual(calls, []);
g.done();
""")
        self.run_page(r"""
g.fetch = async () => { throw new Error("must not upload"); };
assert.equal((await run(big(UPLOAD_MAX_BYTES + 1)))[0].message, "文件超过 30 MB");
g.done();
""", lang="zh")

    def test_a_file_at_the_limit_is_sent(self):
        self.run_page(r"""
g.fetch = async (url: string) => { calls.push(String(url)); return { ok: true, status: 200, json: async () => ({ message: {
  fileId: "upl_1", filename: "scan-50.pdf", mountPath: "/mnt/session/uploads/upl_1/scan-50.pdf", size_bytes: UPLOAD_MAX_BYTES, pages: 50 } }) }; };
const steps = await run(big(UPLOAD_MAX_BYTES));
assert.deepEqual(steps.map((s: any) => s.type), ["running", "requires-action"]);
assert.equal(calls.length, 1);
g.done();
""")

    def test_a_proxy_refusing_the_body_reads_as_too_large_not_something_went_wrong(self):
        # 413 是网关 / Frappe 回的 HTML，没有 _server_messages：照实说超限，不说「出错了」
        self.run_page(r"""
g.fetch = async () => ({ ok: false, status: 413, json: async () => { throw new SyntaxError("html"); } });
const steps = await run(big(1024));
assert.equal(steps.at(-1).type, "incomplete");
assert.equal(steps.at(-1).message, "File is larger than 30 MB");
await assert.rejects(uploadMethod("pearnly_core.express_operator.api.intake", { company: "c" }, big(1024)),
                     (err: any) => err.message === "File is larger than 30 MB");
g.done();
""")


if __name__ == "__main__":
    unittest.main()
