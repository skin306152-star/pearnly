"""Windows 小助手（`companion/`）与服务端之间的约定：两边在同一个仓库里，改一边另一边必须跟上。

只读源码（AST / 文本），不导入 Frappe、不起服务，CI 离线跑。守住的是 2026-09-27 那次漏掉的几处：
- 小助手调用的三个机器接口 = 服务端 `http_auth` 认的三个原生地址，且都是只收 POST 的白名单方法；
- 对话页向本站申请的这台电脑的密钥（`companion.api.register`，只收 POST）以 `密钥@本站域名` 交给桌面端，
  小助手的 `split_pairing_code` 能拆开（2026-10-10 起网页不再显示 / 复制配对码）；
- 下载地址三处一致：小助手查更新、nginx 下载目录、发布接收脚本，以及网页「下载助手」的默认地址；
- 文件夹同步（2026-10 CC 闭环第一批，`folder_intake/machine.py` ↔ `folder_worker.py`）：四个机器接口、对话提醒的字段、
  新增的请求 / 回复键两边都在，小助手按云端下发的顺序跑任务，提醒点开的地址与共用层深链接同一形状。
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


if __name__ == "__main__":
    unittest.main()
