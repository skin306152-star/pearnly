"""Windows 小助手（`companion/`）与服务端之间的约定：两边在同一个仓库里，改一边另一边必须跟上。

只读源码（AST / 文本），不导入 Frappe、不起服务，CI 离线跑。守住的是 2026-09-27 那次漏掉的几处：
- 小助手调用的三个机器接口 = 服务端 `http_auth` 认的三个原生地址，且都是只收 POST 的白名单方法；
- 对话页向本站申请的这台电脑的密钥（`companion.api.register`，只收 POST）以 `密钥@本站域名` 交给桌面端，
  小助手的 `split_pairing_code` 能拆开（2026-10-10 起网页不再显示 / 复制配对码）；
- 下载地址三处一致：小助手查更新、nginx 下载目录、发布接收脚本，以及网页「下载助手」的默认地址；
- 文件夹同步（2026-10 CC 闭环第一批，`folder_intake/machine.py` ↔ `folder_worker.py`）：四个机器接口、对话提醒的字段、
  新增的请求 / 回复键两边都在，小助手按云端下发的顺序跑任务，提醒点开的地址与共用层深链接同一形状；
- POS 导出（第二批 L9）：两边收同一组扩展名（含登记即失败的 .xls）、云端按任务下发、小助手按它扫与数（老任务新收的
  扩展名先补拍底）；载荷 3 的 `pos` 块两边同形、票号上限一致，小助手的拒收码（含 POS_INVALID）云端认作「要人处理」且有
  译文，销项税摘要带「อย่างย่อ」给报税草稿认简易税票。
"""
import ast
import importlib.util
import re
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "companion" / "src" / "companion"
SERVER = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "companion"
FOLDER_SERVER = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "folder_intake"


def module_constants(path):
    """模块顶层的字符串常量（支持 `A = B + "…"` 这种拼接）。"""
    values = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                value = eval(compile(ast.Expression(node.value), str(path), "eval"), {"Path": Path}, dict(values))
            except Exception:
                continue
            if isinstance(value, (str, dict, tuple, Path)):
                values[node.targets[0].id] = value
    return values


def helper_function(path, name, needs):
    """只抽出小助手里的一个函数（连同它用到的常量）来执行——CI 的 Python 没装小助手的依赖。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keep = [node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name == name)
            or (isinstance(node, ast.Assign) and any(getattr(t, "id", "") in needs for t in node.targets))]
    namespace = {"re": re}
    exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), namespace)
    return namespace[name]


def whitelisted_post(path, function):
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef) and node.name == function:
            return any("frappe.whitelist" in ast.unparse(d) and "'POST'" in ast.unparse(d)
                       for d in node.decorator_list)
    return False


class MachineEndpoints(unittest.TestCase):
    def test_the_helper_calls_exactly_the_paths_the_server_authenticates(self):
        helper = module_constants(COMPANION / "queue_client.py")
        server = module_constants(SERVER / "http_auth.py")
        self.assertEqual({helper["_HEARTBEAT"], helper["_LEASE"], helper["_ACK"]},
                         {"/api/method/" + method for method in server["METHODS"].values()})

    def test_each_path_is_a_post_only_whitelisted_method(self):
        for method in module_constants(SERVER / "http_auth.py")["METHODS"].values():
            module, function = method.rsplit(".", 1)
            path = ROOT / "apps" / "pearnly_cowork" / Path(*module.split(".")).with_suffix(".py")
            self.assertTrue(whitelisted_post(path, function), method)

    def test_the_health_check_uses_the_same_heartbeat(self):
        text = (COMPANION / "health_check.py").read_text(encoding="utf-8")
        self.assertIn("from .queue_client import _HEARTBEAT", text)
        self.assertNotIn("/api/erp/agent", text)


class PairingCode(unittest.TestCase):
    def test_the_chat_page_hands_key_at_this_site_and_the_helper_splits_it(self):
        page = (ROOT / "frontend/assistant/src/lib/desktop.ts").read_text(encoding="utf-8")
        self.assertIn('fetch("/api/method/pearnly_cowork.companion.api.register"', page)
        self.assertIn('api.call("pair", { code: `${found.token}@${location.host}`', page)
        self.assertTrue(whitelisted_post(SERVER / "api.py", "register"))
        web = (ROOT / "apps/pearnly_cowork/pearnly_cowork/public/desk/targets.js").read_text(encoding="utf-8")
        self.assertNotIn("copy_to_clipboard(`${token}@", web)
        split_pairing_code = helper_function(COMPANION / "pairing.py", "split_pairing_code",
                                             {"_HOST", "_LOCAL_HOSTS"})
        self.assertEqual(split_pairing_code("exp_CWEC-1_secret@testcowork.pearnly.com"),
                         ("exp_CWEC-1_secret", "https://testcowork.pearnly.com"))

    def test_the_server_token_shape_never_contains_the_site(self):
        # 服务端只认 `exp_` 开头、字符里没有 @ 的密钥；小助手发出去的正是 @ 之前那段。
        header = re.search(r"_HEADER = re.compile\(r'(.+)'\)", (SERVER / "http_auth.py").read_text(encoding="utf-8"))
        self.assertIsNotNone(header)
        self.assertIsNone(re.fullmatch(header.group(1), "Bearer exp_CWEC-1_s@testcowork.pearnly.com"))
        self.assertIsNotNone(re.fullmatch(header.group(1), "Bearer exp_CWEC-1_s"))


class Downloads(unittest.TestCase):
    def test_update_check_nginx_receiver_and_web_default_agree(self):
        updater = module_constants(COMPANION / "updater.py")
        latest = updater["DOWNLOAD_SITE"] + updater["_LATEST_PATH"]
        self.assertEqual(latest, "https://pearnly.com/downloads/companion/latest.json")
        receiver = module_constants(ROOT / "infrastructure/production/receive-companion.py")
        self.assertEqual(receiver["URL"], "/downloads/companion")
        conf = (ROOT / "infrastructure/production/landing.conf").read_text(encoding="utf-8")
        location = re.search(r"location \^~ /downloads/companion/ \{\s*alias ([^;]+);", conf)
        self.assertEqual(location.group(1), str(receiver["ROOT"]) + "/")
        self.assertIn("location = /static/companion/latest.json", conf)  # 旧版小助手查更新的地址
        api = module_constants(SERVER / "api.py")
        self.assertEqual(api["INSTALLER_URL"],
                         updater["DOWNLOAD_SITE"] + receiver["URL"] + "/" + receiver["NAME"])


def method_source(path, cls, name):
    """类里一个方法的源码（只读 AST，不导入）。"""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == name:
                    return ast.unparse(item)
    raise AssertionError(f"{cls}.{name} not found in {path}")


def quoted(text, key):
    return re.search(r"""["']%s["']""" % re.escape(key), text) is not None


class FolderSync(unittest.TestCase):
    """文件夹同步的报文约定（docs/work/2026-10-12-cc-closed-loop.md「第一批接口约定」E 节；向后兼容，协议号不变）。"""

    def setUp(self):
        self.worker = (COMPANION / "folder_worker.py").read_text(encoding="utf-8")
        self.server = "\n".join((FOLDER_SERVER / name).read_text(encoding="utf-8")
                                for name in ("machine.py", "contracts.py"))

    def test_the_helper_calls_exactly_the_folder_paths_the_server_authenticates(self):
        helper = module_constants(COMPANION / "folder_client.py")
        server = module_constants(SERVER / "http_auth.py")
        self.assertEqual({helper[name] for name in ("SYNC", "BEGIN", "FINISH", "WORKBOOK")},
                         {"/api/method/" + method for method in server["FOLDER_METHODS"].values()})
        for method in server["FOLDER_METHODS"].values():
            module, function = method.rsplit(".", 1)
            self.assertTrue(whitelisted_post(ROOT / "apps" / "pearnly_cowork" / Path(*module.split(".")).with_suffix(".py"),
                                             function), method)

    def test_notice_fields_are_the_same_on_both_sides(self):
        helper = module_constants(COMPANION / "folder_worker.py").get("NOTICE_FIELDS")
        server = module_constants(FOLDER_SERVER / "contracts.py").get("NOTICE_FIELDS")
        self.assertEqual(helper, ("id", "title", "text", "created", "session"))
        self.assertEqual(server, helper)

    def test_the_helper_sends_the_new_request_keys(self):
        round_source = method_source(COMPANION / "folder_worker.py", "FolderIntake", "round")
        sync = next(node for node in ast.walk(ast.parse(round_source)) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "client.sync")
        keys = {key.value for key in sync.args[0].keys}
        self.assertTrue({"roots", "runs", "rule_health", "rule_pending", "notices_after"} <= keys, keys)
        report = method_source(COMPANION / "folder_worker.py", "FolderIntake", "_report")
        self.assertIn("line['counts']", report)
        self.assertIn("line['partial']", report)
        self.assertIn("record['slot']", method_source(COMPANION / "folder_worker.py", "FolderIntake", "_run"))

    def test_the_server_reads_the_new_request_keys(self):
        for key in ("rule_pending", "notices_after", "counts", "partial", "slot"):
            self.assertTrue(quoted(self.server, key), key)

    def test_the_server_sends_the_new_reply_keys(self):
        for key in ("existing", "notices", "companion_latest", "rule_names"):
            self.assertTrue(quoted(self.server, key), key)

    def test_the_helper_reads_the_new_reply_keys(self):
        round_source = method_source(COMPANION / "folder_worker.py", "FolderIntake", "round")
        self.assertIn("rule.get('existing')", round_source)
        self.assertIn("response.get('notices')", round_source)
        self.assertIn("response.get('rule_names')", round_source)
        self.assertIn("self._offer(response)", round_source)
        self.assertIn("_offer_update(response", self.worker)
        self.assertIn('hb.get("companion_latest")', (COMPANION / "worker.py").read_text(encoding="utf-8"))

    def test_the_helper_runs_rules_in_the_order_the_server_sends(self):
        # F7：同一账套的存货进货任务由云端排在销售之前；小助手照下发顺序跑，不另排序。
        self.assertIn('for rule in response.get("rules") or []:', self.worker)
        round_source = method_source(COMPANION / "folder_worker.py", "FolderIntake", "round")
        self.assertNotIn("sorted(", round_source)
        self.assertNotIn(".sort(", round_source)

    def test_an_interrupted_run_is_reported_without_scanned(self):
        # 小助手一次运行中途被叫停（note=interrupted）不带 scanned，下次启动接着跑、用同一个运行号：云端只在带了 scanned
        # 时记扫描完成，同一运行号的后一次上报更新同一条运行记录（完成简报等它跑完，不按不全的数字先发）。
        tree = ast.parse((FOLDER_SERVER / "machine.py").read_text(encoding="utf-8"))
        record = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_record_run")
        writes = [node for node in ast.walk(record) if isinstance(node, ast.Assign)
                  and any(ast.unparse(target) == "doc.scanned_at" for target in node.targets)]
        guards = [node for node in ast.walk(record) if isinstance(node, ast.If)
                  and ast.unparse(node.test) == "row.get('scanned')"]
        self.assertEqual(len(writes), 1)
        self.assertTrue(guards and writes[0] in list(ast.walk(guards[0])))
        self.assertIn("frappe.db.exists(RUN, key)", ast.unparse(record))
        run = method_source(COMPANION / "folder_worker.py", "FolderIntake", "_run_folder")
        self.assertIn("record['note'] = 'interrupted'", run)

    def test_pos_exports_use_the_same_extensions_and_are_sent_per_task(self):
        helper = module_constants(COMPANION / "folder_plan.py")
        server = module_constants(FOLDER_SERVER / "contracts.py")
        for name in ("EXTENSIONS", "TABLE_EXTENSIONS"):
            self.assertEqual(helper[name], server[name], name)
        self.assertIn(server["LEGACY_TABLE"], server["TABLE_EXTENSIONS"])
        self.assertTrue(quoted(self.server, "extensions") and quoted(self.server, "table_counts"))
        # 老任务只扫拍过底的扩展名：新收的（升级后开始收 POS 导出）先补拍底，早就在文件夹里的表格不上传
        self.assertIn("plan.taken_extensions(rule, state)", method_source(COMPANION / "folder_worker.py", "FolderIntake",
                                                                          "_run_folder"))
        self.assertIn("baseline_exts", method_source(COMPANION / "folder_worker.py", "FolderIntake", "_baseline"))
        self.assertIn("line['table_counts']", method_source(COMPANION / "folder_worker.py", "FolderIntake", "_report"))

    def test_reminder_links_match_the_shared_deep_link(self):
        # 托盘提醒点开的地址 = 共用层 assistant/contracts.deep_link（网页推送点开的同一个地址）接在公司站点上。
        spec = importlib.util.spec_from_file_location(
            "assistant_contracts", ROOT / "apps/pearnly_core/pearnly_core/assistant/contracts.py")
        shared = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shared)
        self.assertTrue(callable(getattr(shared, "deep_link", None)), "assistant/contracts.deep_link")
        # 只抽小助手 tomo_links 里的函数与常量执行（CI 的 Python 没装小助手的依赖）
        tree = ast.parse((COMPANION / "tomo_links.py").read_text(encoding="utf-8"))
        keep = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.Assign))]
        namespace = {}
        exec("import json, os, re\nfrom pathlib import Path\nfrom urllib.parse import quote, urlsplit", namespace)
        exec(compile(ast.Module(body=keep, type_ignores=[]), "tomo_links.py", "exec"), namespace)
        for session in ("sess_1:abc", "", "bad id"):
            helper = urlsplit(namespace["conversation_url"]("https://acme.pearnly.com", session))
            server = urlsplit("https://acme.pearnly.com" + shared.deep_link(session))
            self.assertEqual((helper.netloc, helper.path, parse_qs(helper.query)),
                             (server.netloc, server.path, parse_qs(server.query)), session)


def number_constants(path, names):
    """模块顶层的几个常量（数字、元组、字典，可用前面定义过的常量拼）：只读 AST，不导入。"""
    values = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = eval(compile(ast.Expression(node.value), str(path), "eval"),
                                                  {"__builtins__": {}}, dict(values))
            except Exception:
                continue
    return {name: values.get(name) for name in names}


class OnDemandRequests(unittest.TestCase):
    """CC 桌面端按需请求（2026-10 CC 闭环）：服务端 `companion/request_*` ↔ 小助手 `request_client` / `request_worker`
    / `request_contract` / `folder_fetch`。"""

    SHARED = ("KINDS", "CHUNK_BYTES", "MAX_BYTES", "LEASE_SECONDS", "POLL_SECONDS")

    def test_the_helper_calls_exactly_the_request_paths_the_server_authenticates(self):
        helper = module_constants(COMPANION / "request_client.py")
        server = module_constants(SERVER / "http_auth.py")
        self.assertEqual({helper["NEXT"], helper["COMPLETE"]},
                         {"/api/method/" + method for method in server["REQUEST_METHODS"].values()})
        for method in server["REQUEST_METHODS"].values():
            module, function = method.rsplit(".", 1)
            self.assertTrue(whitelisted_post(ROOT / "apps" / "pearnly_cowork" / Path(*module.split(".")).with_suffix(".py"),
                                             function), method)

    def test_this_helper_is_new_enough_to_take_requests(self):
        """服务端只把 `MIN_COMPANION` 及以上的电脑当作「会领按需请求」：仓库里这版小助手不能低于它。"""
        version = module_constants(COMPANION / "version.py")["VERSION"]
        minimum = module_constants(SERVER / "request_contracts.py")["MIN_COMPANION"]
        self.assertGreaterEqual(tuple(int(part) for part in version.split(".")),
                                tuple(int(part) for part in minimum.split(".")))

    def test_both_sides_share_the_same_kinds_sizes_and_timings(self):
        helper = number_constants(COMPANION / "request_contract.py", self.SHARED)
        server = number_constants(SERVER / "request_contracts.py", self.SHARED)
        self.assertEqual(helper, server)
        self.assertEqual(set(server["MAX_BYTES"]), set(server["KINDS"]))
        self.assertEqual(set(server["LEASE_SECONDS"]), set(server["KINDS"]))
        # 一块 base64 后仍在 Frappe 默认 25MB 请求上限与租户 nginx 30m 之内
        self.assertLess(4 * ((server["CHUNK_BYTES"] + 2) // 3) + 1024, 25 * 1024 * 1024)
        self.assertEqual(server["MAX_BYTES"]["folder_fetch"],
                         number_constants(ROOT / "apps/pearnly_core/pearnly_core/assistant/contracts.py",
                                          ("UPLOAD_MAX_BYTES",))["UPLOAD_MAX_BYTES"])

    def test_the_helper_handles_only_known_kinds_and_resumes_on_the_servers_offset_code(self):
        worker = (COMPANION / "request_worker.py").read_text(encoding="utf-8")
        handlers = re.search(r"HANDLERS: dict = \{(.*?)\n\}", worker, re.S).group(1)
        kinds = set(re.findall(r'"([a-z_]+)":', handlers))
        self.assertTrue(kinds and kinds <= set(number_constants(SERVER / "request_contracts.py", ("KINDS",))["KINDS"]))
        code = module_constants(COMPANION / "request_worker.py")["OFFSET_CODE"]
        self.assertIn(f'"code": "{code}", "received": received', (SERVER / "request_service.py").read_text(encoding="utf-8"))
        round_source = method_source(COMPANION / "request_worker.py", "RequestWorker", "round")
        sent = next(node for node in ast.walk(ast.parse(round_source)) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "client.next")
        self.assertEqual({key.value for key in sent.args[0].keys}, {"version", "kinds", "device"})
        contracts = (SERVER / "request_contracts.py").read_text(encoding="utf-8")
        for key in ("version", "kinds", "device", "request", "handle", "ok", "code", "offset", "content", "final",
                    "size", "sha256", "meta"):
            self.assertTrue(quoted(contracts, key), key)

    def test_fetchable_types_are_the_same_on_the_helper_the_server_and_chat_attachments(self):
        helper = module_constants(COMPANION / "folder_fetch.py")["FETCH_EXTENSIONS"]
        server = module_constants(FOLDER_SERVER / "contracts.py")["FETCH_EXTENSIONS"]
        self.assertEqual(helper, server)
        spec = importlib.util.spec_from_file_location(
            "assistant_contracts_for_requests", ROOT / "apps/pearnly_core/pearnly_core/assistant/contracts.py")
        shared = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(shared)
        self.assertEqual(set(server), set(shared.UPLOAD_EXTENSIONS))

    def test_folder_only_keys_may_take_requests_and_the_server_filters_by_erp(self):
        auth = (SERVER / "http_auth.py").read_text(encoding="utf-8")
        self.assertIn("if operation not in FOLDER_METHODS and operation not in REQUEST_METHODS and", auth)
        server = number_constants(SERVER / "request_contracts.py", ("ADAPTER_KINDS", "FOLDER_KINDS", "KINDS"))
        self.assertEqual(server["ADAPTER_KINDS"], {"express": server["KINDS"], "mrerp": server["FOLDER_KINDS"]})
        self.assertNotIn("express_read", server["FOLDER_KINDS"])
        self.assertIn("contracts.allowed_kinds(adapter", (SERVER / "request_service.py").read_text(encoding="utf-8"))

    def test_the_request_thread_is_started_and_stopped_with_the_folder_thread(self):
        tray = (COMPANION / "gui_tray.py").read_text(encoding="utf-8")
        self.assertIn("self._requests = RequestWorker(lambda: self.configs)", tray)
        for name in ("_stop_for_update", "_quit"):
            self.assertIn("self._requests.stop()", method_source(COMPANION / "gui_tray.py", "TrayApp", name))
        for name in ("_resume_after_update",):
            self.assertIn("self._requests.start()", method_source(COMPANION / "gui_tray.py", "TrayApp", name))
        self.assertIn("requests.running()", method_source(COMPANION / "gui_tray.py", "TrayApp", "_work_stopped"))
        self.assertIn("requests.busy()", method_source(COMPANION / "gui_tray.py", "TrayApp", "_busy"))


def method_source_of(path, name):
    """模块里一个顶层函数的源码（只读 AST，不导入）。"""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found in {path}")


class ExpressReadback(unittest.TestCase):
    """回读 Express 账套（2026-10 CC 闭环 L1）：服务端 `readback` 发的读账参数、小助手 `express_read` 交回的读账结果，两边
    同一组键、同一个结果版本与点名单据号上限；小助手读账不碰「等账套」。"""

    READBACK = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "readback"

    def test_the_helper_reads_the_parameters_the_server_sends(self):
        tree = ast.parse((self.READBACK / "contracts.py").read_text(encoding="utf-8"))
        builder = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "read_params")
        sent = next(node.value for node in ast.walk(builder) if isinstance(node, ast.Return))
        keys = {key.value for key in sent.keys}
        self.assertEqual(keys, {"account_set", "account_root", "period", "docnums"})
        helper = method_source_of(COMPANION / "express_read.py", "read_books")
        self.assertEqual(set(re.findall(r"params\.get\('([a-z_]+)'\)", helper)), keys)

    def test_both_sides_share_the_result_version_and_the_named_document_limits(self):
        names = ("RESULT_VERSION", "DOCNUMS_MAX", "DOCNUM_MAX")
        self.assertEqual(number_constants(COMPANION / "express_read.py", names),
                         number_constants(self.READBACK / "contracts.py", names))
        server = number_constants(SERVER / "request_contracts.py", ("PARAMS_MAX",))["PARAMS_MAX"]
        # 点名单据号放满（每条最长）再加两条路径，仍在按需请求的参数上限之内
        limits = number_constants(self.READBACK / "contracts.py", ("DOCNUMS_MAX", "DOCNUM_MAX", "PATH_MAX"))
        self.assertLess(limits["DOCNUMS_MAX"] * (limits["DOCNUM_MAX"] + 4) + 2 * limits["PATH_MAX"] + 200, server)

    def test_the_server_reads_every_part_the_helper_sends(self):
        helper = method_source_of(COMPANION / "express_read.py", "collect")
        returned = next(node for node in ast.walk(ast.parse(helper)) if isinstance(node, ast.Return)
                        and isinstance(node.value, ast.Dict))
        parts = {key.value for key in returned.value.keys} | {"account_set", "read_at"}
        parser = (self.READBACK / "contracts.py").read_text(encoding="utf-8")
        for part in parts - {"version", "period"}:
            self.assertTrue(quoted(parser, part), part)
        for part in ("version", "period"):
            self.assertIn(f'raw.get("{part}")', parser)

    def test_the_server_compares_with_what_the_helper_wrote(self):
        """差异表按 CC 桌面版写进 Express 的样子比（2026-10-13）：进项票号的缩法两边同一个结果；分录换科目要的账套设置
        （现金 / 存货 / 购货科目、盘存制）与对方档科目，读账交回、服务端认得，也正是写入时换科目用的那几个。"""
        helper = helper_function(COMPANION / "purchase_adapter.py", "fit_ref_no", {"REF_NO_BYTES"})
        spec = importlib.util.spec_from_file_location(
            "pushed_for_contract", ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "push" / "express"
            / "pushed.py")
        pushed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pushed)
        for value in ("", None, "INV-9", " G0012-69-0045871 ", "AB/CD.EF GH-IJKLMNOPQ", "ใบกำกับภาษีเลขที่-12345",
                      "x" * 40, "A B-C/D.E" * 3):
            self.assertEqual(pushed.fit_ref_no(value), helper(value), value)

        collect = ast.parse(method_source_of(COMPANION / "express_read.py", "collect"))
        settings = next({key.value for key in node.keys} for node in ast.walk(collect) if isinstance(node, ast.Dict)
                        and any(getattr(key, "value", None) == "cash" for key in node.keys))
        server = number_constants(self.READBACK / "contracts.py", ("SETTINGS",))["SETTINGS"]
        self.assertEqual(settings, set(server) | {"periodic"})
        source = ast.unparse(collect)
        for column in ("ACCNUM00", "ACCNUM04", "ACCNUM18", "ISPERPETUA"):
            self.assertIn(f"'{column}'", source)
        self.assertIn("'party_account'", source)
        self.assertTrue(quoted((self.READBACK / "contracts.py").read_text(encoding="utf-8"), "party_account"))
        writer = (COMPANION / "dbf_writer.py").read_text(encoding="utf-8")
        sales = (COMPANION / "dbf_sales.py").read_text(encoding="utf-8")
        for slot in ("ACCNUM00", "ACCNUM04", "ACCNUM18"):
            self.assertIn(f'default_account(account_dir, "{slot}")', writer)
        self.assertIn('default_account(account_dir, "ACCNUM00")', sales)
        self.assertRegex(writer, r'control_account\(\s*account_dir, "APMAS"')
        self.assertRegex(sales, r'control_account\(\s*account_dir, "ARMAS"')
        self.assertIn('"ISPERPETUA"', (COMPANION / "account_rules.py").read_text(encoding="utf-8"))
        self.assertIn('ref_no=fit_ref_no(payload.get("ref_no"))',
                      (COMPANION / "purchase_adapter.py").read_text(encoding="utf-8"))

    def test_reading_never_waits_for_the_account_set_lock(self):
        tree = ast.parse((COMPANION / "express_read.py").read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names} | {node.module or "" for node in ast.walk(tree)
                                                if isinstance(node, ast.ImportFrom)}
        self.assertFalse({"account_lock", "is_account_busy"} & imported)
        called = {ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}
        self.assertFalse({name for name in called if "is_account_busy" in name or "account_lock" in name})
        tables = number_constants(COMPANION / "express_read.py", ("TABLES",))["TABLES"]
        self.assertNotIn("ISRUN", tables)
        self.assertIn('"express_read": express_read.read_books', (COMPANION / "request_worker.py").read_text(
            encoding="utf-8"))


def _literal(path, name):
    """模块顶层常量的字面值（`frozenset({…})` 取里面的集合）。"""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "frozenset":
                return frozenset(ast.literal_eval(value.args[0]))
            return ast.literal_eval(value)
    raise AssertionError(name)


class PosPayload(unittest.TestCase):
    """载荷 3（POS 导出的日汇总票）：版本、`pos` 块的键、拒收码与摘要字样两边一致。"""

    COWORK = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork"

    def test_the_helper_accepts_the_version_the_server_sends(self):
        server = _literal(self.COWORK / "contracts" / "pos.py", "PAYLOAD_VERSION")
        helper = _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS")
        self.assertEqual(server, 3)
        self.assertEqual(max(helper), server)

    def test_the_pos_block_keys_match(self):
        mapper = (self.COWORK / "push" / "express" / "sales_mapper.py").read_text(encoding="utf-8")
        adapter = (COMPANION / "sales_adapter.py").read_text(encoding="utf-8")
        for key in ("first", "last", "count", "docnum", "cash", "transfers", "acc", "amount", "label"):
            self.assertTrue(quoted(mapper, key) and quoted(adapter, key), key)
        # 票号起止的长度上限两边一致（云端截短，小助手的校验永远能过）
        self.assertEqual(_literal(self.COWORK / "contracts" / "pos.py", "NUMBER_MAX"),
                         _literal(COMPANION / "sales_adapter.py", "_POS_NUMBER_MAX"))
        self.assertIn('"auto"', mapper)
        self.assertIn('"auto"', adapter)

    def test_the_helper_refusals_are_for_a_person_and_translated(self):
        constants = module_constants(COMPANION / "pos_journal.py")
        lifecycle = (self.COWORK / "push" / "lifecycle.py").read_text(encoding="utf-8")
        terms = (self.COWORK / "i18n" / "messages.tsv").read_text(encoding="utf-8")
        codes = [constants[name] for name in ("ERR_JOURNAL_BOOK", "ERR_ACCOUNT")]
        codes.append(_literal(COMPANION / "sales_adapter.py", "ERR_POS_INVALID"))
        for code in codes:
            self.assertTrue(quoted(lifecycle, code), code)
            self.assertIn("\tintake.error." + code + "\n", terms)
        # `pos` 块不合格时 write_sale 直接以 POS_INVALID 回报（不混进通用的 PAYLOAD_INVALID）
        self.assertIn("ERR_POS_INVALID if v.error_code == ERR_POS_INVALID",
                      (COMPANION / "dbf_sales.py").read_text(encoding="utf-8"))
        self.assertIn("อย่างย่อ", constants["VAT_DESCRIPTION"])


if __name__ == "__main__":
    unittest.main()
