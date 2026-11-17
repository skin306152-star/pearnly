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
- ERP 前台（2026-10-22）：按需请求 `express_masters`（整套主档 + 各仓结存）两边同一组种类常量、参数键与版本门槛，
  结果属于连接与账套；载荷 4（明细行 / 表头 `loccod`）小助手认得、仓库码不对回 `ERR_LOCATION`（要人处理）。
- 自动更新被 Windows 拦下（教程问题第 1 条，2026-10-15）：心跳与文件夹同步都带 `update_blocked`（版本 + UTC 时刻，
  null = 没被拦，旧版不带），服务端同一套收法（`connections/desktop_update.py`）；「下载新版」与官网同一个安装包地址。
"""
import ast
import importlib.util
import re
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "companion" / "src" / "companion"
SERVER = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "companion"
# CC 桌面版线上约定的纯函数在共用层（ADR-025）
WIRE = ROOT / "apps" / "pearnly_core" / "pearnly_core" / "cc_desktop" / "wire"
RULES = ROOT / "apps" / "pearnly_core" / "pearnly_core" / "express_rules" / "push"
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


class DeviceMethods(unittest.TestCase):
    """认电脑的两条机器方法（ADR-021；服务端第 1 批，CC 桌面版 1.7.0 第 5 批）：表与另外三张不重叠、都是只收 POST 的白名单
    方法，MR.ERP 的只读文件夹密钥也放行；桌面端调的正是这两条路径、报文键与服务端收的一致、状态同名，403 / 404 当旧服务端、
    401 当密钥被拒（1.9.1 起，都不当成要删成员）；设备密钥的形状服务端收；桥的状态里有设备密钥（对话页每次打开带它调 register，页面一侧在第 6 批）。"""

    DEVICE_CONTRACTS = SERVER / "device_contracts.py"
    LINK = COMPANION / "device_link.py"

    def test_the_server_table_is_whitelisted_post_and_apart_from_the_other_three(self):
        server = module_constants(SERVER / "http_auth.py")
        devices = server["DEVICE_METHODS"]
        self.assertEqual(set(devices), {"device_hello", "device_leave"})
        others = {**server["METHODS"], **server["FOLDER_METHODS"], **server["REQUEST_METHODS"]}
        self.assertFalse(set(devices) & set(others))
        self.assertFalse(set(devices.values()) & set(others.values()))
        # 第五张表：MR.ERP 登录在 CC 里授权（ADR-026 第 7 条，`credential_api.mrerp_login`），也是只收 POST 的白名单方法
        logins = server["LOGIN_METHODS"]
        self.assertEqual(set(logins), {"mrerp_login"})
        self.assertFalse(set(logins.values()) & (set(others.values()) | set(devices.values())))
        # 第六张表：代录 AI 接手的模型请求（`operator_api.operator_ai`，2026-11-03），只收 POST，只读文件夹密钥不放行
        operators = server["OPERATOR_METHODS"]
        self.assertEqual(set(operators), {"operator_ai"})
        self.assertFalse(set(operators) & set(server["FOLDER_ONLY_ALLOWED"]))
        self.assertEqual(server["MACHINE_METHODS"], {**others, **devices, **logins, **operators})
        for method in {**logins, **operators}.values():
            module, function = method.rsplit(".", 1)
            self.assertTrue(whitelisted_post(ROOT / "apps" / "pearnly_cowork" / Path(*module.split(".")).with_suffix(".py"),
                                             function), method)
        for method in devices.values():
            module, function = method.rsplit(".", 1)
            self.assertTrue(whitelisted_post(ROOT / "apps" / "pearnly_cowork" / Path(*module.split(".")).with_suffix(".py"),
                                             function), method)

    def test_the_desktop_calls_exactly_the_two_device_paths(self):
        helper = module_constants(self.LINK)
        server = module_constants(SERVER / "http_auth.py")["DEVICE_METHODS"]
        self.assertEqual(helper["HELLO"], "/api/method/" + server["device_hello"])
        self.assertEqual(helper["LEAVE"], "/api/method/" + server["device_leave"])

    def test_the_hello_body_and_the_statuses_are_the_same_on_both_sides(self):
        server = module_constants(self.DEVICE_CONTRACTS)
        helper = module_constants(self.LINK)
        for name in ("ATTACHED", "CLAIMED_ELSEWHERE", "LEFT", "SUPERSEDED"):
            self.assertEqual(helper[name], server[name], name)
        request = method_source_of(self.DEVICE_CONTRACTS, "hello_request")
        returned = next(node.value for node in ast.walk(ast.parse(request)) if isinstance(node, ast.Return))
        body = next(node.value for node in ast.walk(ast.parse(method_source_of(self.LINK, "hello_body")))
                    if isinstance(node, ast.Return))
        self.assertEqual({key.value for key in body.keys}, {key.value for key in returned.keys})
        self.assertEqual(number_constants(self.DEVICE_CONTRACTS, ("MAX_HOST_NAMES",))["MAX_HOST_NAMES"],
                         number_constants(COMPANION / "device_facts.py", ("MAX_HOST_NAMES",))["MAX_HOST_NAMES"])
        # 电脑名与文件夹同步、绑定里报的同一个（queue_client._device_name）
        self.assertIn("from .queue_client import _device_name", self.LINK.read_text(encoding="utf-8"))
        self.assertIn('"name": _device_name()', method_source_of(self.LINK, "hello_body").replace("'", '"'))

    def test_the_device_key_the_desktop_makes_is_one_the_server_accepts(self):
        pattern = re.search(r"_DEVICE_KEY = re.compile\(r'(.+)'\)", (SERVER / "contracts.py").read_text(encoding="utf-8"))
        self.assertIsNotNone(pattern)
        import base64
        import secrets
        namespace = {"base64": base64, "secrets": secrets}
        tree = ast.parse(self.LINK.read_text(encoding="utf-8"))
        keep = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "new_device_key"]
        exec(compile(ast.Module(body=keep, type_ignores=[]), str(self.LINK), "exec"), namespace)
        for _ in range(50):
            self.assertIsNotNone(re.fullmatch(pattern.group(1), namespace["new_device_key"]()))

    def test_401_is_a_rejected_key_and_only_403_404_from_the_device_methods_mean_an_old_server(self):
        # 1.9.1：服务端认不出密钥（吊销、移除，`credentials.authenticate` → AuthenticationError）是 401，不是旧服务端；
        # 403（总闸 / 停用，`_deny_permission`）与 404（没有这条方法）才是
        helper = number_constants(self.LINK, ("REFUSED", "REJECTED"))
        self.assertEqual(set(helper["REFUSED"]), {403, 404})
        self.assertEqual(set(helper["REJECTED"]), {401})
        post = method_source_of(self.LINK, "_post")
        self.assertLess(post.index("if response.status_code in REJECTED:"), post.index("if response.status_code in REFUSED:"))
        self.assertIn("raise KeyRejected(", post)
        self.assertIn("raise OldServer(", post)
        self.assertIn("frappe.throw(_('companion.unauthorized'), frappe.AuthenticationError)",
                      (SERVER / "http_auth.py").read_text(encoding="utf-8"))
        self.assertIn("http_status_code = 403", (SERVER / "http_auth.py").read_text(encoding="utf-8"))
        # 断开：服务端吊销不了（旧版、连不上）也照样只删本机成员
        disconnect = method_source(COMPANION / "desktop_settings.py", "DesktopSettings", "disconnect")
        self.assertLess(disconnect.index("_leave_member("), disconnect.index("remove_profile("))
        self.assertNotIn("if not left", disconnect)

    def test_the_bridge_hands_the_device_key_to_the_chat_page(self):
        snapshot = method_source(COMPANION / "desktop_settings.py", "DesktopSettings", "snapshot")
        self.assertIn("'device': device", snapshot)
        self.assertIn('"devices"', (COMPANION / "tomo_bridge.py").read_text(encoding="utf-8"))
        self.assertIn("device=device_link.identity", (COMPANION / "gui_tray.py").read_text(encoding="utf-8"))
        identity = method_source_of(self.LINK, "identity")
        self.assertIn("'key': key", identity)

    def test_the_chat_page_registers_with_the_bridges_device_key_on_every_open(self):
        """第 5 节第 7 条（ADR-021 第 6 批，页面一侧）：对话页从桥的 state() 取 ``device``（features 含 ``devices`` 才算），
        每次打开都带 ``device_key`` / ``device_name`` 调 register v2（与服务端参数同名），成员的 ``device_state`` 触发
        ``rekey``；两端认的成员状态名相同；旧版桥照旧不带设备密钥。"""
        page = (ROOT / "frontend/assistant/src/lib/desktop.ts").read_text(encoding="utf-8")
        api = (SERVER / "api.py").read_text(encoding="utf-8")
        register = next(node for node in ast.parse(api).body if isinstance(node, ast.FunctionDef) and node.name == "register")
        params = {arg.arg for arg in register.args.args}
        for key in ("device_key", "device_name", "rekey", "rejoin"):
            self.assertIn(key, params)
            self.assertIn(f"{key}: ", page)
        # rejoin（第 6 批审查第 2 条）：被移除的电脑只有人点「连接」才带，打开页面的自动登记不带
        self.assertIn("{ device_key: device.key, device_name: device.name, ...(rekey ? { rekey: true } : {}), "
                      "...(rejoin ? { rejoin: true } : {}) }", page)
        self.assertIn('export const DEVICES = "devices";', page)
        self.assertIn('"devices"', (COMPANION / "tomo_bridge.py").read_text(encoding="utf-8"))
        self.assertIn("state?.features?.includes(DEVICES) && key", page)
        # 两端的成员状态名一致（服务端 device_contracts，桌面端 device_link 照抄标到成员上）；页面见到 superseded 带 rekey，
        # 别的状态（claimed_elsewhere）先按新电脑登记，回 exists 再 rekey
        contracts = (SERVER / "device_contracts.py").read_text(encoding="utf-8")
        link = self.LINK.read_text(encoding="utf-8")
        for state in ("superseded", "claimed_elsewhere"):
            self.assertIn(f"'{state}'", contracts)
            self.assertIn(state, link)
        self.assertIn('member.device_state === "superseded"', page)
        # 每次打开都登记（有设备密钥时不只在没有成员时），本机状态变了、新出现 device_state 再登记一次
        hook = page[page.index("export function useAutoConnect"):]
        self.assertIn("if (!membersHere(state).length || thisComputer(state)) await connectComputer(api);", hook)
        self.assertIn("off = api.onChanged(", hook)

    def test_replacing_a_members_key_lets_the_old_key_say_hello_not_leave(self):
        """第 6 批审查第 5 条：配对按端点换掉成员的旧密钥前先拿旧密钥认一次电脑（服务端回 superseded 才吊销它、把旧电脑并进这台），
        用的是 device_hello、不是 device_leave（同一配对码抄到两台时 leave 会吊销另一台正在用的密钥）；被说过不能再用的不发。"""
        pair = method_source(COMPANION / "desktop_settings.py", "DesktopSettings", "pair")
        self.assertLess(pair.index("self._settle_member("), pair.index("self._pair("))
        settle = method_source(COMPANION / "desktop_settings.py", "DesktopSettings", "_settle_member")
        self.assertIn("getattr(record, 'device_state', '')", settle)
        self.assertNotIn("_leave", settle)
        link = method_source_of(self.LINK, "settle_old_key")
        self.assertIn("hello(backend_url, token, hello_body(key", link)
        self.assertNotIn("LEAVE", link)

    def test_register_takes_the_device_key_and_the_management_rpcs_are_gone(self):
        api = (SERVER / "api.py").read_text(encoding="utf-8")
        register = next(node for node in ast.parse(api).body if isinstance(node, ast.FunctionDef) and node.name == "register")
        self.assertEqual([arg.arg for arg in register.args.args],
                         ["request_key", "express", "replace", "expected_generation", "device_key", "device_name", "rekey",
                          "rejoin"])
        for retired in ("issue", "revoke", "status"):
            self.assertFalse(any(isinstance(node, ast.FunctionDef) and node.name == retired
                                 for node in ast.parse(api).body), retired)


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
        # 安装包地址共用一份（`pearnly_core.cc_desktop.release`，盘点 2026-11-08 P2-2），Cowork 的接口引用它
        release = module_constants(ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/release.py")
        self.assertEqual(release["INSTALLER_URL"],
                         updater["DOWNLOAD_SITE"] + receiver["URL"] + "/" + receiver["NAME"])
        self.assertIn("INSTALLER_URL = release.INSTALLER_URL", (SERVER / "api.py").read_text(encoding="utf-8"))


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
        self.assertEqual(helper, ("id", "title", "text", "created", "session", "view"))
        self.assertEqual(server, helper)
        # view 是 1.9.1 加的：桌面端收旧服务端不带它的提醒；共用层同步给的每条都带（形状同 deep_link 认的）
        self.assertEqual(module_constants(COMPANION / "folder_worker.py").get("NOTICE_OPTIONAL"), ("view",))
        listing = method_source_of(ROOT / "apps/pearnly_core/pearnly_core/assistant/notices.py", "for_references")
        self.assertIn("'view': contracts.notice_view(row.get('view') or '')", listing)

    def test_a_reminder_of_one_company_opens_the_page_the_server_links_to(self):
        """旧交接 T3（1.9.1）：托盘点开属于某家公司、没挂对话的提醒，地址与共用层 deep_link 同形（``?company=<编号>``），
        公司编号的形状两端一样；对话页开着时交给 window.tomo.open 的页内深链接由 lib/nav.ts 的 useDeepLinks 接手。"""
        core = (ROOT / "apps/pearnly_core/pearnly_core/assistant/contracts.py").read_text(encoding="utf-8")
        server = re.search(r'NOTICE_PROJECT_VIEW = re.compile\(r"(.+)"\)', core).group(1)
        helper = re.search(r'COMPANY_VIEW = re.compile\(r"(.+)"\)',
                           (COMPANION / "tomo_links.py").read_text(encoding="utf-8")).group(1)
        self.assertEqual(server.replace("company:", "company:(", 1).rstrip("$") + ")$", helper)
        self.assertEqual(module_constants(COMPANION / "tomo_links.py")["NOTICE_VIEWS"],
                         module_constants(ROOT / "apps/pearnly_core/pearnly_core/assistant/contracts.py")["NOTICE_VIEWS"])
        self.assertIn('return "/assistant?company=" + quote(view.split(":", 1)[1], safe="")', core)
        nav = (ROOT / "frontend/assistant/src/lib/nav.ts").read_text(encoding="utf-8")
        self.assertIn('const link = id.startsWith("/assistant") ? targetOf(id) : null;', nav)
        self.assertIn('parsed.searchParams.get("company")', nav)
        self.assertIn("open: (target: string) => openTarget(target),",
                      (ROOT / "frontend/assistant/src/lib/desktop.ts").read_text(encoding="utf-8"))

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
        minimum = module_constants(WIRE / "request_contracts.py")["MIN_COMPANION"]
        self.assertGreaterEqual(tuple(int(part) for part in version.split(".")),
                                tuple(int(part) for part in minimum.split(".")))

    def test_both_sides_share_the_same_kinds_sizes_and_timings(self):
        helper = number_constants(COMPANION / "request_contract.py", self.SHARED)
        server = number_constants(WIRE / "request_contracts.py", self.SHARED)
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
        self.assertTrue(kinds and kinds <= set(number_constants(WIRE / "request_contracts.py", ("KINDS",))["KINDS"]))
        code = module_constants(COMPANION / "request_worker.py")["OFFSET_CODE"]
        self.assertIn(f'"code": "{code}", "received": received', (SERVER / "request_service.py").read_text(encoding="utf-8"))
        round_source = method_source(COMPANION / "request_worker.py", "RequestWorker", "round")
        sent = next(node for node in ast.walk(ast.parse(round_source)) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "client.next")
        self.assertEqual({key.value for key in sent.args[0].keys}, {"version", "kinds", "device"})
        contracts = (WIRE / "request_contracts.py").read_text(encoding="utf-8")
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
        # MR.ERP 代录开着时 MR.ERP 密钥也放行心跳 / 领单 / 回执（ADR-026 第 3 步，11-03 选 A）；关着只放行下面这张表
        self.assertIn("if not folder_only_allows(operation) and", auth)
        self.assertIn("return operation in FOLDER_ONLY_ALLOWED or (operator_contracts.MRERP_OPERATOR_ENABLED and operation in METHODS)",
                      auth)
        self.assertIn("FOLDER_ONLY_ALLOWED = {**FOLDER_METHODS, **REQUEST_METHODS, **DEVICE_METHODS, **LOGIN_METHODS}", auth)
        server = number_constants(WIRE / "request_contracts.py", ("ADAPTER_KINDS", "FOLDER_KINDS", "KINDS"))
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
        server = number_constants(WIRE / "request_contracts.py", ("PARAMS_MAX",))["PARAMS_MAX"]
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


class WriteCheck(unittest.TestCase):
    """写完顺手核对（数字会计员第 3 期第 6 条）：CC 桌面版 `write_verify` 回执 ``meta.verify {state, fields, party}``，服务端
    `push.express.written_check` 只收这三个键、同一组不符项；随回执进 task_journal、重发照样带回；自动推只派给能回核对的版本。"""

    PUSH = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "push"

    def test_both_sides_name_the_same_mismatches(self):
        helper = number_constants(COMPANION / "write_verify.py", ("FIELDS", "STATE_OK", "STATE_MISMATCH"))
        server = number_constants(RULES / "written_check.py", ("COMPANION_FIELDS",))
        self.assertEqual(helper["FIELDS"], server["COMPANION_FIELDS"])
        self.assertIn(f'OK, MISMATCH, UNVERIFIED = "{helper["STATE_OK"]}", "{helper["STATE_MISMATCH"]}", "unverified"',
                      (RULES / "written_check.py").read_text(encoding="utf-8"))

    def test_the_receipt_carries_exactly_state_fields_and_party(self):
        result = method_source_of(COMPANION / "write_verify.py", "result")
        for key in ("state", "fields", "party"):
            self.assertTrue(quoted(result, key), key)
        clean = method_source_of(RULES / "written_check.py", "clean")
        for key in ("state", "fields", "party", "tax_id", "branch"):
            self.assertTrue(quoted(clean, key), key)
        self.assertIn("out['verify'] = verify", method_source_of(RULES / "common.py",
                                                                   "sanitize_push_meta"))
        meta = method_source_of(COMPANION / "worker.py", "_push_meta")
        self.assertIn("m['verify'] = dict(res.verify)", meta)
        # task_journal 存的就是整份 meta：重发（duplicate_skipped）照样带回 verify
        handle = method_source_of(COMPANION / "worker.py", "_handle_dbf_task")
        self.assertIn("task_journal.remember(task_journal.key_of(task), {'docnum': res.docnum, 'line_modes': res.line_modes, 'meta': meta})",
                      handle)
        self.assertIn("meta={**(done.get('meta') or {}), 'written': False, 'duplicate_skipped': True}", handle)

    def test_the_operator_submenu_receipt_keys_are_the_ones_the_server_keeps(self):
        """代录停在定制菜单的子菜单（11-05 修法 D）：CC 宿主回执 meta 带 doc_type / menu_options，worker 放进 meta.operator；
        服务端回执净化只放行这两项（`common.clean_operator_meta`）。"""
        result = method_source(COMPANION / "express_operator" / "host.py", "Host", "_result")
        self.assertIn("meta.update(doc_type=doc.doc_type, menu_options=list(doc.menu_options))", result)
        handle = method_source_of(COMPANION / "worker.py", "_handle_operator_task")
        self.assertIn("meta = {'operator': {**(result.get('meta') or {})", handle)
        clean = method_source_of(RULES / "common.py", "clean_operator_meta")
        for key in ("doc_type", "menu_options"):
            self.assertTrue(quoted(clean, key), key)
        self.assertIn("out[OPERATOR_META] = operator", method_source_of(RULES / "common.py", "sanitize_push_meta"))
        self.assertIn('OPERATOR_META = "operator"', (RULES / "common.py").read_text(encoding="utf-8"))

    def test_the_half_document_receipt_keys_are_the_ones_the_server_keeps(self):
        """代录保存途中停住（11-14，接缝见 docs/work/2026-11-14-cc-operator-half-doc.md）：服务端净化放行 meta.operator 的 partial
        {docnum, missing} 与 held {computer, account_set}；原因 partial_saved 两边都登记；两产品未完成的单只多显示这个单号。"""
        clean = method_source_of(RULES / "common.py", "clean_operator_meta")
        for key in ("partial", "held", "docnum", "missing", "computer", "account_set"):
            self.assertTrue(quoted(clean, key), key)
        core = (ROOT / "apps" / "pearnly_core" / "pearnly_core" / "express_operator" / "contracts.py").read_text(encoding="utf-8")
        self.assertIn('"partial_saved": HUMAN', core)
        self.assertIn('"partial_saved": HUMAN', (COMPANION / "express_operator" / "contract.py").read_text(encoding="utf-8"))
        sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
        from pearnly_core.express_rules.push.common import clean_operator_meta, operator_partial_docnum

        got = clean_operator_meta({"partial": {"docnum": " IV0000045 ", "missing": ["total", "journal", "x"]},
                                   "held": {"computer": "PC1", "account_set": "TEST", "pid": 1}, "evidence": "C:/x.png"})
        self.assertEqual(got, {"partial": {"docnum": "IV0000045", "missing": ["total", "journal"]},
                               "held": {"computer": "PC1", "account_set": "TEST"}})
        # 从存下的回执 meta 读（Cowork 推送记录 response_json.meta、ERP 写账件 meta_json）
        self.assertEqual(operator_partial_docnum({"operator": {"partial": {"docnum": "IV0000045"}}}), "IV0000045")
        self.assertEqual(operator_partial_docnum({"operator": {"menu_options": ["x"]}}), "")
        self.assertEqual(operator_partial_docnum(None), "")
        apps = ROOT / "apps"
        cowork = method_source_of(apps / "pearnly_cowork" / "pearnly_cowork" / "assistant" / "operator_source.py", "status")
        self.assertIn("halves[len(out)] = doc.get(\"last_push_record\")", cowork.replace("'", '"'))
        erp = method_source_of(apps / "pearnly_erp_portal" / "pearnly_erp_portal" / "express" / "operator.py", "status")
        self.assertIn("halves[len(out)] = row.ledger_job", erp)
        # 迟到终态（接缝 e）：只收「成功 + 还是未完成 + 单号与记下的半截单号一致」，两套账本都走这一个判定
        from pearnly_core.express_rules.push.common import late_partial_success

        stored = {"operator": {"partial": {"docnum": "IV0000045"}}}
        self.assertTrue(late_partial_success(True, "manual", stored, " IV0000045 "))
        for args in ((False, "manual", stored, "IV0000045"), (True, "queued", stored, "IV0000045"),
                     (True, "manual", stored, "IV0000046"), (True, "manual", {}, "IV0000045"), (True, "manual", stored, "")):
            self.assertFalse(late_partial_success(*args), args)
        self.assertIn("late_partial_success(", method_source_of(apps / "pearnly_core" / "pearnly_core" / "cc_desktop" / "jobs.py",
                                                                "_late_partial"))
        self.assertIn("common.late_partial_success(", method_source_of(apps / "pearnly_cowork" / "pearnly_cowork" / "push" / "native"
                                                                       / "service.py", "_late_partial"))

    def test_cc_desktop_sends_the_half_document_keys_the_server_keeps(self):
        """CC 宿主回执 meta 带 partial（缺什么的取值与服务端同一组）与 held。"""
        result = method_source(COMPANION / "express_operator" / "host.py", "Host", "_result")
        self.assertIn("meta['partial'] = dict(partial)", result)
        self.assertIn("meta['held'] = self._held_info(", result)
        gaps = module_constants(RULES / "common.py")["OPERATOR_PARTIAL_GAPS"]
        book = module_constants(COMPANION / "express_operator" / "book.py")
        self.assertEqual(set(gaps), {book["GAP_TOTAL"], book["GAP_LINES"], book["GAP_JOURNAL"], book["GAP_VAT"]})
        # 迟到终态（接缝 e）：同一回执号（投递号）、成功、带原单号
        idle = method_source_of(COMPANION / "worker.py", "operator_idle")
        self.assertIn("qc.ack_success(item['ack'], extracted_invoice_no=item['docnum']", idle)
        self.assertIn("operator_host.host().note_late(", method_source_of(COMPANION / "worker.py", "_ack_operator"))

    def test_automatic_pushes_only_go_to_a_version_that_returns_the_check(self):
        server = module_constants(self.PUSH / "automation" / "rules.py")["VERIFY_MIN_VERSION"]
        helper = module_constants(COMPANION / "version.py")["VERSION"]
        self.assertGreaterEqual(tuple(map(int, helper.split("."))), tuple(map(int, server.split("."))))
        for writer in ("dbf_writer.py", "dbf_sales.py"):
            self.assertIn("verify=verify", (COMPANION / writer).read_text(encoding="utf-8"), writer)

    def test_account_history_v2_leaves_out_what_pearnly_wrote(self):
        # 科目那部分在 _accounts 里读（第五轮审查后入账方式另外读，collect 只把两部分接起来）
        collect = method_source_of(COMPANION / "account_history.py", "_accounts")
        self.assertIn("_pearnly(text(get(rec, columns, 'USERID')))", collect)
        self.assertIn("_pearnly(text(get(rec, columns, 'CREBY')))", collect)
        self.assertIn("PEARNLY_USERID", (COMPANION / "account_history.py").read_text(encoding="utf-8"))
        self.assertEqual(module_constants(COMPANION / "dbf_schema.py")["PEARNLY_USERID"], "PEARNLY")


class AccountHistory(unittest.TestCase):
    """科目建议 B（2026-10-15）：服务端 `readback.history` 请电脑读一本账套近 12 个月「供应商 → 常用科目」，小助手
    `account_history` 交回汇总。两边同一组参数、结果版本与上限；读的时候不碰「等账套」；结果属于连接与账套。"""

    READBACK = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "readback"

    def test_the_helper_reads_the_parameters_the_server_sends(self):
        tree = ast.parse((self.READBACK / "history_rules.py").read_text(encoding="utf-8"))
        builder = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "read_params")
        sent = next(node.value for node in ast.walk(builder) if isinstance(node, ast.Return))
        keys = {key.value for key in sent.keys}
        self.assertEqual(keys, {"account_set", "account_root", "since"})
        helper = method_source_of(COMPANION / "account_history.py", "read_history")
        self.assertEqual(set(re.findall(r"params\.get\('([a-z_]+)'\)", helper)), keys)
        # 服务端给的「从哪天起」在小助手收的范围里
        server = number_constants(self.READBACK / "history_rules.py", ("SINCE_DAYS",))["SINCE_DAYS"]
        self.assertLessEqual(server, number_constants(COMPANION / "account_history.py",
                                                      ("SINCE_DAYS_MAX",))["SINCE_DAYS_MAX"])

    def test_both_sides_share_the_result_version_and_limits(self):
        names = ("RESULT_VERSION", "SUPPLIERS_MAX", "ACCOUNTS_PER_SUPPLIER")
        self.assertEqual(number_constants(COMPANION / "account_history.py", names),
                         number_constants(self.READBACK / "history_rules.py", names))

    def test_the_server_reads_every_part_the_helper_sends(self):
        collect = ast.parse(method_source_of(COMPANION / "account_history.py", "_assemble"))
        sent = set()
        for node in ast.walk(collect):
            if isinstance(node, ast.Dict):
                sent |= {key.value for key in node.keys if isinstance(key, ast.Constant)}
        parser = (self.READBACK / "history_rules.py").read_text(encoding="utf-8")
        for part in ("version", "read_at", "since", "suppliers", "name", "tax_id", "branch", "accounts", "code",
                     "docs", "last"):
            self.assertIn(part, sent | {"read_at"}, part)
            self.assertTrue(quoted(parser, part), part)
        self.assertIn('data["read_at"] =', method_source_of(COMPANION / "account_history.py", "read_history")
                      .replace("'", '"'))

    def test_the_v3_goods_posting_has_the_same_shape_on_both_sides(self):
        """结果版本 3（用户 10-17）：每家供应商可带 ``posting`` ``{stock|service: {docs, last}}``，两边同一组记法；服务端只在 v3
        起收它，判档前经同一个钩子只读存着的那份。"""
        helper = (COMPANION / "account_history.py").read_text(encoding="utf-8")
        self.assertIn('row["posting"] =', helper)
        shape = ast.parse(method_source_of(COMPANION / "account_history.py", "_no_posting"))
        kinds = {key.value for node in ast.walk(shape) if isinstance(node, ast.Dict) for key in node.keys
                 if isinstance(key, ast.Constant)}
        self.assertEqual(kinds, {"stock", "service", "docs", "last"})
        server = number_constants(self.READBACK / "history_rules.py", ("POSTING_KINDS", "POSTING_VERSION"))
        self.assertEqual(set(server["POSTING_KINDS"]), {"stock", "service"})
        self.assertEqual(server["POSTING_VERSION"], number_constants(COMPANION / "account_history.py",
                                                                      ("RESULT_VERSION",))["RESULT_VERSION"])
        parser = method_source_of(self.READBACK / "history_rules.py", "parse")
        self.assertIn("item.get('posting')", parser)  # ast.unparse 用单引号
        # 两张表真可选（审查 10-17）：不进科目那部分一起复制、读坏整份重读的 TABLES，另外复制另外读，出事只是不交 posting；
        # 读坏、坏行、被占的几条路由 companion 自己的测试锁住（test_account_history）
        tables = number_constants(COMPANION / "account_history.py", ("TABLES", "REQUIRED", "POSTING_TABLES"))
        self.assertFalse(set(tables["POSTING_TABLES"]) & set(tables["TABLES"]))
        self.assertFalse(set(tables["POSTING_TABLES"]) & set(tables["REQUIRED"]))
        self.assertIn("_posting_from(folder", method_source_of(COMPANION / "account_history.py", "read_history"))
        push = (ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "push" / "application" / "service.py").read_text(
            encoding="utf-8")
        self.assertIn('.get("posting")', push)

    def test_reading_never_waits_for_the_account_set_lock_and_only_express_does_it(self):
        tree = ast.parse((COMPANION / "account_history.py").read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names} | {node.module or "" for node in ast.walk(tree)
                                                if isinstance(node, ast.ImportFrom)}
        self.assertFalse({"account_lock", "is_account_busy", "dbf_writer"} & imported)
        self.assertIn('"account_history": account_history.read_history',
                      (COMPANION / "request_worker.py").read_text(encoding="utf-8"))
        server = number_constants(WIRE / "request_contracts.py", ("KINDS", "FOLDER_KINDS", "SHARED_KINDS"))
        self.assertIn("account_history", server["KINDS"])
        self.assertNotIn("account_history", server["FOLDER_KINDS"])  # MR.ERP 的文件夹成员不读账套
        self.assertEqual(server["SHARED_KINDS"], ("account_history", "express_masters", "express_marks", "express_ar_open"))
        # 推送服务按钩子取，不导入 readback（readback 依赖 push）
        hooks = (ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('cowork_push_account_history = ["pearnly_cowork.readback.history.for_push"]', hooks)
        push = (ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "push" / "application" / "service.py").read_text(
            encoding="utf-8")
        self.assertIn('frappe.get_hooks("cowork_push_account_history")', push)
        self.assertNotIn("pearnly_cowork.readback", push)


def _literal(path, name):
    """模块顶层常量的字面值（`frozenset({…})` 取里面的集合）。"""
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            value = node.value
            if isinstance(value, ast.Call) and getattr(value.func, "id", "") == "frozenset":
                return frozenset(ast.literal_eval(value.args[0]))
            return ast.literal_eval(value)
    raise AssertionError(name)


class PartyPrenames(unittest.TestCase):
    """对方名称的法人前缀：服务端给载荷 prename（`express_rules/push/common._PRENAMES`），CC 桌面版建对方档时按自己那份
    （`master_create._PRENAMES`）把前缀从名称里拆掉、PRENAM 写载荷给的值。两份不一致就丢字或重复（11-15：服务端少「ร้าน」，
    「ร้านทดสอบ 1115」写成 CUSNAM「ทดสอบ 1115」、PRENAM 空）。"""

    def test_both_sides_know_the_same_prefixes_in_the_same_order(self):
        self.assertEqual(_literal(RULES / "common.py", "_PRENAMES"), _literal(COMPANION / "master_create.py", "_PRENAMES"))

    def test_what_cc_strips_is_what_the_server_gives_as_prename(self):
        cc = _literal(COMPANION / "master_create.py", "_PRENAMES")
        server = _literal(RULES / "common.py", "_PRENAMES")

        def first(prefixes, name):  # 两边同一种取法：按表的顺序取第一个开头对上的
            return next((p for p in prefixes if name.startswith(p)), "")

        for name in ("ร้านทดสอบ 1115", "บริษัท ลูกค้า จำกัด", "บริษัทจำกัด ทดสอบ", "หจก.ทดสอบ", "หจก ทดสอบ",
                     "ห้างหุ้นส่วนจำกัด ทดสอบ", "หสน.ทดสอบ", "นาย ทดสอบ", "ทดสอบ"):
            stripped = first(cc, name)
            core = name[len(stripped):].strip()
            prename = first(server, name)
            self.assertEqual(prename, stripped, name)
            self.assertEqual((prename + core).replace(" ", ""), name.replace(" ", ""), name)  # PRENAM + 核心名 = 原名，不丢字


class OperatorMastersReachTheProduct(unittest.TestCase):
    """代录回执里随单新建的主档（11-16 验收：代录新建客户 เ001 写成 IV0000030，ERP 客户的码一直空、同步回来另建一条）：CC 桌面版
    放在 ``meta.operator.masters``（``[{kind: party / item, code, name?}]``，1.12.21 已发出）；服务端两条读法都认它——机器通道作业视图
    （ERP，`common.written_codes`）与 Cowork 存回执前的净化（`sanitize_push_meta` 留下它、顶层没有对方码时照它补）。"""

    def test_the_desktop_shape_is_the_one_the_server_reads(self):
        worker = (COMPANION / "worker.py").read_text(encoding="utf-8")
        self.assertIn('{"masters": ready.created}', worker)
        masters = (COMPANION / "express_operator" / "masters.py").read_text(encoding="utf-8")
        self.assertIn('created.append({"kind": "party", "code": plan.party_code})', masters)
        self.assertIn('created.append({"kind": "item", "code": line["code"], "name": line["name"]})', masters)
        sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
        from pearnly_core.express_rules.push import common

        self.assertEqual(set(common.OPERATOR_MASTER_KINDS), {"party", "item"})
        sent = {"operator": {"masters": [{"kind": "party", "code": "เ001"}, {"kind": "item", "code": "ST-1", "name": "ข้าว"},
                                         {"kind": "repaired", "code": "ST-2"}]}}
        self.assertEqual(common.written_codes(sent), {"party_code": "เ001", "line_codes": [{"name": "ข้าว", "code": "ST-1"}]})
        clean = common.sanitize_push_meta(sent)
        self.assertEqual(clean["party_code"], "เ001")
        self.assertEqual([row["kind"] for row in clean["operator"]["masters"]], ["party", "item"])
        # 直写回执顶层有的照旧以顶层为准
        self.assertEqual(common.written_codes({"party_code": "C-9", **sent})["party_code"], "C-9")
        channel = (ROOT / "apps" / "pearnly_core" / "pearnly_core" / "cc_desktop" / "jobs.py").read_text(encoding="utf-8")
        self.assertIn("codes = written_codes(meta)", channel)


class StockGroupDecidedByTheServer(unittest.TestCase):
    """新建库存品挂哪个存货科目组（11-16 真机 H5）：服务端在账套已有库存品时也按合格名单定（`stock_acc_group.for_new_items`），
    定下的组随载荷 ``stock_acccod`` 交给 CC 桌面版；各版 CC 都先用它、不再按众数选。两端的键名、库存行模式值、「没带物料码 = 新建」
    这三处对上，服务端定的组才真的落到新建的品上。"""

    PUSH = ROOT / "apps" / "pearnly_core" / "pearnly_core" / "express_rules" / "push"

    def test_the_group_the_server_decides_is_the_one_the_desktop_builds_with(self):
        for adapter in ("purchase_adapter.py", "sales_adapter.py"):
            self.assertIn('stock_acccod=str(payload.get("stock_acccod") or "").strip()',
                          (COMPANION / adapter).read_text(encoding="utf-8"), adapter)
        # 选过的（服务端带来的）先用，没带才看众数；带来的组按一个开关决定要不要再过存货科目判据（原生实录前不合 main）
        template = (COMPANION / "stock_template.py").read_text(encoding="utf-8")
        self.assertIn("code = chosen or dominant", template)
        self.assertIn("if not chosen or GUARD_CHOSEN_GROUP:", template)

    def test_unfit_groups_are_offered_only_when_the_desktop_reports_them(self):
        # 服务端能记住的组 = 小助手报上来的全部组（合格的在前）；自动定组只数合格的。CC 桌面版上报时不再只报合格的
        rule = (ROOT / "apps" / "pearnly_core" / "pearnly_core" / "express_rules" / "targets" / "stock_groups.py").read_text(
            encoding="utf-8")
        self.assertIn("for g in choosable(groups)}", rule)
        for path in ("catalog_probe.py", "express_masters.py"):
            self.assertNotIn('if c["fit"]]', (COMPANION / path).read_text(encoding="utf-8"), path)
            self.assertNotIn('if row["fit"]]', (COMPANION / path).read_text(encoding="utf-8"), path)
        for mapper in ("mapper.py", "sales_mapper.py"):
            self.assertIn('payload["stock_acccod"] = stock_acccod', (self.PUSH / mapper).read_text(encoding="utf-8"), mapper)

    def test_stock_line_modes_and_new_items_mean_the_same_on_both_ends(self):
        modes = number_constants(self.PUSH / "common.py", ("ITEM_MODE_STOCK",))["ITEM_MODE_STOCK"]
        sale = number_constants(self.PUSH / "sales_mapper.py", ("ITEM_MODE_STOCK_SALE",))["ITEM_MODE_STOCK_SALE"]
        self.assertIn(f'"{modes}"', (COMPANION / "dbf_detail.py").read_text(encoding="utf-8"))
        self.assertIn(f'"{sale}"', (COMPANION / "stock_sale.py").read_text(encoding="utf-8"))
        # 服务端按「库存行没带物料码」判这张单要新建：CC 桌面版对没带码的库存行先按品名找、找不到才建档（建档时挂上面那个组）
        rule = (self.PUSH / "stock_acc_group.py").read_text(encoding="utf-8")
        self.assertIn('not str(item.get("code") or "").strip()', rule)


class Release1122Edges(unittest.TestCase):
    """CC 桌面版 1.12.22 那一批的三条接缝（11-15）：服务端先上线，旧版（1.12.21）与新版都要对。各条按「仓库里这版 CC 认不认」
    分两种写法核：认了就两端同名同值；还不认时服务端不能把它交给这版。"""

    ERP = ROOT / "apps" / "pearnly_erp_portal" / "pearnly_erp_portal"

    def version(self):
        return tuple(int(part) for part in module_constants(COMPANION / "version.py")["VERSION"].split("."))

    def test_invoice_numbers_go_only_to_a_cc_that_takes_the_docnums_parameter(self):
        # 未收发票按单号查（已收清 / 已作废 / 账套里没有）：旧版对多出来的参数回 params_invalid，服务端按版本只派给新版
        minimum = number_constants(WIRE / "request_contracts.py", ("PARAM_MIN_COMPANION",))["PARAM_MIN_COMPANION"]
        floor = tuple(int(part) for part in minimum["express_ar_open"]["docnums"].split("."))
        optional = number_constants(COMPANION / "express_receivables.py", ("OPTIONAL_KEYS",))["OPTIONAL_KEYS"]
        self.assertEqual("docnums" in optional, self.version() >= floor)
        if "docnums" in optional:
            states = number_constants(self.ERP / "collection" / "snapshot.py", ("STATES",))["STATES"]
            helper = number_constants(COMPANION / "express_receivables.py", ("LOOKUP_STATES", "DOCNUMS_MAX"))
            self.assertEqual(set(helper["LOOKUP_STATES"]), set(states))
            self.assertEqual(helper["DOCNUMS_MAX"],
                             number_constants(self.ERP / "collection" / "snapshot.py", ("DOCNUMS_MAX",))["DOCNUMS_MAX"])
            self.assertIn('"looked_up"', (COMPANION / "express_receivables.py").read_text(encoding="utf-8"))

    def test_the_wake_up_in_the_lease_reply_is_read_by_name_or_ignored(self):
        # 领单回复的 requests_waiting：新版读这个名字叫醒按需请求线程；旧版只按名字取 jobs / master_refresh，多一个键不碍事
        wire = (WIRE / "queue_contracts.py").read_text(encoding="utf-8")
        self.assertIn('result["requests_waiting"] = True', wire)
        poll = method_source(COMPANION / "queue_client.py", "QueueClient", "poll")
        self.assertIn("response.get('jobs')", poll)  # 方法源码经 AST 还原，引号是单引号
        self.assertNotIn("set(response)", poll)
        if 'requests_waiting' in poll:
            self.assertIn("response.get('requests_waiting')", poll)

    def test_the_party_prefix_is_optional_and_joined_by_the_server(self):
        # 主档上报的 prename（PRENAM）：服务端按「前缀 + 核心名」显示，没带（旧版）就是核心名；按编码认档不变
        pick = (COMPANION / "express_masters.py").read_text(encoding="utf-8")
        contract = (self.ERP / "express" / "contracts.py").read_text(encoding="utf-8")
        self.assertIn('row.get("prename")', contract)
        if '"prename"' in pick:
            self.assertIn('"PRENAM"', pick)


class Release1123Edges(unittest.TestCase):
    """CC 桌面版 1.12.23 代录中途的框（③）：回执带框的原文、按钮与指纹，服务端放行；人答过的答案经载荷下发、按指纹照点。
    旧版（1.12.22 及以前）遇到同样的框照旧回 unknown_screen、不带 dialog；载荷里多出的 dialog_answers 被忽略。"""

    def test_the_dialog_choice_reason_and_its_receipt_shape_match(self):
        sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
        sys.path.insert(0, str(COMPANION.parent))
        from companion.express_operator import contract as client
        from companion.express_operator.engine import dialog_info
        from pearnly_core.express_operator import contracts as server
        from pearnly_core.express_rules.push import common

        self.assertEqual(client.REASONS.get("dialog_choice"), client.HUMAN)
        self.assertEqual(server.REASONS.get("dialog_choice"), server.HUMAN)
        box = {"title": "ExpressI", "texts": ["เลือกวิธีคำนวณ", "แบบ A", "แบบ B", "ยกเลิก"], "buttons": ["แบบ &A", "แบบ B", "ยกเลิก"]}
        dialog = dialog_info(box, "rr", "saving")
        self.assertEqual(dialog["buttons"], ["แบบ A", "แบบ B", "ยกเลิก"])
        self.assertEqual(dialog["doc_type"], "RR")
        self.assertEqual(len(dialog["digest"]), 16)
        self.assertEqual(dialog_info(box, "RR", "editing")["digest"], dialog["digest"])  # 指纹不含在哪一步
        kept = common.clean_operator_meta({"dialog": dialog, "reason": "dialog_choice"})["dialog"]
        self.assertEqual(kept, dialog)  # 服务端原样放行 CC 给的形状（长度上限两边相同）
        from companion.express_operator import engine

        self.assertEqual((engine.DIALOG_TEXT_MAX, engine.DIALOG_BUTTONS_MAX, engine.DIALOG_BUTTON_MAX),
                         (common.OPERATOR_DIALOG_TEXT, common.OPERATOR_DIALOG_BUTTONS, common.OPERATOR_DIALOG_BUTTON))

    def test_the_interrupted_reason_and_where_it_stopped_match(self):
        sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
        sys.path.insert(0, str(COMPANION.parent))
        from companion.express_operator import contract as client
        from companion.express_operator.engine import Interrupted
        from pearnly_core.express_operator import contracts as server
        from pearnly_core.express_rules.push import common

        self.assertEqual((client.REASONS.get("interrupted"), server.REASONS.get("interrupted")), (client.HUMAN, server.HUMAN))
        stop = Interrupted("line2.price", "cell is '9', want '10'", "editing")
        self.assertEqual(stop.reason, "interrupted")
        self.assertEqual(common.clean_operator_meta({"interrupt": stop.interrupt})["interrupt"], stop.interrupt)

    def test_the_server_sends_remembered_answers_in_the_shape_the_desktop_reads(self):
        sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
        sys.path.insert(0, str(COMPANION.parent))
        from companion.express_operator.draft import MAX_DIALOG_ANSWERS, dialog_answers as client
        from pearnly_core.express_operator import contracts as server

        batch = {"id": "b-1", "index": 1, "total": 1}
        plain = server.for_operator({"doctype": "RR"}, batch)
        self.assertNotIn("dialog_answers", plain["operator"])  # 没记住的：载荷与以前相同
        given = {"0123456789ABCDEF": " แบบ ข ", "bad": "x", "fedcba9876543210": ""}
        sent = server.for_operator({"doctype": "RR"}, batch, dialog_answers_by_digest=given)["operator"]["dialog_answers"]
        self.assertEqual(sent, {"0123456789abcdef": "แบบ ข"})
        self.assertEqual(client(sent), sent)  # CC 按同样的规矩收下
        self.assertEqual(server.DIALOG_ANSWERS_MAX, MAX_DIALOG_ANSWERS)

    def test_the_page_reports_the_open_conversation_with_the_bridge_method_cc_reads(self):
        # ⑨：对话页经本机设置桥 call("attention_seen", {session, visible}) 报开着哪段对话、看不看得见；页面开始调时名字与参数两边一致
        bridge = (COMPANION / "tomo_bridge.py").read_text(encoding="utf-8")
        self.assertIn('method == "attention_seen"', bridge)
        self.assertIn('args.get("session")', bridge)
        self.assertIn('args.get("visible")', bridge)
        page = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "frontend" / "assistant" / "src").rglob("*.ts*"))
        if '"attention_seen"' in page:
            self.assertRegex(page, r'"attention_seen",\s*\{[^}]*session[^}]*visible|"attention_seen",\s*\{[^}]*visible[^}]*session')

    def test_remembered_answers_ride_in_operator_dialog_answers(self):
        sys.path.insert(0, str(COMPANION.parent))
        from companion.express_operator.draft import dialog_answers

        good = "0123456789abcdef"
        self.assertEqual(dialog_answers({good: "แบบ B", "short": "x", good.upper()[:15] + "g": "y"}), {good: "แบบ B"})
        self.assertEqual(dialog_answers(["not", "a", "dict"]), {})
        self.assertIn('options.get("dialog_answers")', (COMPANION / "express_operator" / "draft.py").read_text(encoding="utf-8"))

    def test_operator_controls_are_read_by_name_and_the_server_sends_the_same_keys(self):
        # 领单回复的 operator_controls（④）：CC 按名字取、只执行认得的动作；旧服务端不发就什么都不做。服务端开始发时，每条的键
        # （action / batch / doc / digest / button）两边同名，且只发给版本够的电脑
        poll = method_source(COMPANION / "queue_client.py", "QueueClient", "poll")
        self.assertIn("response.get('operator_controls')", poll)
        worker = (COMPANION / "worker.py").read_text(encoding="utf-8")
        for key in ("action", "batch", "doc", "digest", "button"):
            self.assertIn(f'item.get("{key}")', worker)
        wire = (WIRE / "queue_contracts.py").read_text(encoding="utf-8")
        if "operator_controls" in wire:
            for key in ("action", "batch", "doc", "digest", "button"):
                self.assertIn(f'"{key}"', wire)


class PosPayload(unittest.TestCase):
    """载荷 3（POS 导出的日汇总票）：版本、`pos` 块的键、拒收码与摘要字样两边一致。"""

    COWORK = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork"

    def test_the_helper_accepts_the_version_the_server_sends(self):
        server = _literal(ROOT / "apps/pearnly_core/pearnly_core/express_rules/contracts/pos.py", "PAYLOAD_VERSION")
        helper = _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS")
        self.assertEqual(server, 3)
        self.assertIn(server, helper)

    def test_the_stock_transfer_version_and_keys_match(self):
        """调拨 RL（多仓闭环 P2）：ERP 盖的版本 CC 认得；单别、调入仓键两边同名；CC 主档报 RL 单据簿与核过的单别。"""
        erp = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express"
        self.assertIn(_literal(erp / "stock_entry.py", "TRANSFER_PAYLOAD_VERSION"),
                      _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS"))
        self.assertEqual(_literal(erp / "stock_entry.py", "TRANSFER_PAYLOAD_VERSION") > 12, True)
        self.assertEqual(_literal(COMPANION / "dbf_transfer.py", "TRANSFER"), "RL")
        for path in (erp / "stock_entry.py", COMPANION / "dbf_transfer.py"):
            self.assertTrue(quoted(path.read_text(encoding="utf-8"), "to_loccod"), path)
        for path in (erp / "stock_entry.py", COMPANION / "express_masters.py"):
            self.assertTrue(quoted(path.read_text(encoding="utf-8"), "stock_books_checked"), path)
        self.assertIn('"RL"', (COMPANION / "worker.py").read_text(encoding="utf-8"))

    def test_the_new_item_version_and_key_match(self):
        """Express 里没有的商品（多仓闭环 P3x）：ERP 标 `new_ok` 时盖的版本 CC 认得，键两边同名，库存单与调拨都会建档。"""
        erp = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express"
        version = _literal(erp / "stock_entry.py", "NEW_ITEM_PAYLOAD_VERSION")
        self.assertIn(version, _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS"))
        self.assertGreater(version, _literal(erp / "stock_entry.py", "TRANSFER_PAYLOAD_VERSION"))
        for path in (erp / "stock_entry.py", COMPANION / "dbf_stock.py"):
            self.assertTrue(quoted(path.read_text(encoding="utf-8"), "new_ok"), path)
        self.assertIn("create_new_items", (COMPANION / "dbf_transfer.py").read_text(encoding="utf-8"))

    def test_the_mixed_lines_version_matches(self):
        """混行（库存行与非库存行同在一张采购 / 销售单，ERP 按行选入库 / 只记账，11-06）：服务端盖的版本 CC 认得、两边同值；
        CC 不再整单拒收混行；服务端只在真有两种行时才盖。"""
        version = _literal(RULES / "common.py", "MIXED_LINES_PAYLOAD_VERSION")
        self.assertEqual(version, _literal(COMPANION / "payload_contract.py", "MIXED_LINES_PAYLOAD_VERSION"))
        self.assertIn(version, _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS"))
        self.assertGreater(version, _literal(COMPANION / "payload_contract.py", "MRERP_OPERATOR_PAYLOAD_VERSION"))
        for path in (COMPANION / "purchase_lines.py", COMPANION / "dbf_sales.py"):
            self.assertNotIn("V1 不支持", path.read_text(encoding="utf-8"), path)
        sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
        from pearnly_core.express_rules.push import common

        stock, service = {"item_mode": "stock_item"}, {"item_mode": "direct_account"}
        self.assertTrue(common.mixed_lines([stock, service]))
        self.assertTrue(common.mixed_lines([{"item_mode": "stock_sale"}, {"item_mode": "non_stock_item"}]))
        self.assertFalse(common.mixed_lines([stock, stock]))
        self.assertFalse(common.mixed_lines([service]))
        self.assertEqual(common.finalize_payload({"items": [stock, service]})["payload_version"], version)
        self.assertLess(common.finalize_payload({"items": [stock]})["payload_version"], version)

    def test_the_unit_remarks_version_and_key_match(self):
        """按台号码（ARTRNRM，评估 2026-11-02）：服务端带 `remarks` 时盖的版本 CC 认得；键在 ERP 合同、共用写账规则、CC 三处同名；
        一条说明的宽度两边同为 50（ARTRNRM.REMARK C(50)）。"""
        erp = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express"
        version = _literal(RULES / "common.py", "REMARKS_PAYLOAD_VERSION")
        self.assertEqual(version, _literal(COMPANION / "payload_contract.py", "REMARKS_PAYLOAD_VERSION"))
        self.assertIn(version, _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS"))
        self.assertEqual(_literal(erp / "contracts.py", "REMARK_WIDTH"), _literal(COMPANION / "dbf_remarks.py", "MAX_BYTES"))
        for path in (erp / "contracts.py", RULES / "common.py", COMPANION / "dbf_remarks.py"):
            self.assertTrue(quoted(path.read_text(encoding="utf-8"), "remarks"), path)

    def test_the_vat_fields_version_and_key_match(self):
        """载荷 9（ERP 收银日汇总混有免税商品，主会话 10-26）：服务端带 `vat_fields` 时盖的版本 CC 认得、键两边同名。"""
        erp = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/express"
        version = _literal(erp / "pos_day.py", "VAT_FIELDS_PAYLOAD_VERSION")
        self.assertIn(version, _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS"))
        self.assertTrue(quoted((erp / "push.py").read_text(encoding="utf-8"), "vat_fields"))
        self.assertTrue(quoted((COMPANION / "sales_adapter.py").read_text(encoding="utf-8"), "vat_fields"))
        self.assertEqual(_literal(erp / "pos_day.py", "ISVAT_TAXABLE_FIELD"), "AMT01")
        # 免税部分记 AMTRAT0（验收实录第 10 条）：CC 认的金额栏是 AMTnn / AMTRATn，旧版照样写
        self.assertEqual(_literal(erp / "pos_day.py", "ISVAT_EXEMPT_FIELD"), "AMTRAT0")
        # 金额栏的校验进项 / 销项共用（purchase_adapter._validate_vat_fields）；混合税率的采购同样带 vat_fields（10-26）
        self.assertIn("AMTRAT", (COMPANION / "purchase_adapter.py").read_text(encoding="utf-8"))
        self.assertTrue(quoted((COMPANION / "purchase_adapter.py").read_text(encoding="utf-8"), "vat_fields"))
        # 逐行 VAT 设置（ISINFO.VAT_INITEM）：CC 读主档的 facts 与 ERP 读的键同名
        self.assertTrue(quoted((COMPANION / "express_masters.py").read_text(encoding="utf-8"), "vat_initem"))
        self.assertTrue(quoted((erp / "facts.py").read_text(encoding="utf-8"), "vat_initem"))
        # 免税行 / 跨年退货的载荷盖 11，CC 认得（复核 10-26）
        self.assertIn(_literal(ROOT / "apps/pearnly_core/pearnly_core/express_rules/contracts/pos.py", "NATIVE_EXEMPT_PAYLOAD_VERSION"),
                      _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS"))
        # 整单免税的进货单盖 12，CC 认得（主会话 10-29）
        self.assertIn(_literal(ROOT / "apps/pearnly_core/pearnly_core/express_rules/contracts/pos.py",
                               "WHOLE_EXEMPT_PURCHASE_PAYLOAD_VERSION"),
                      _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS"))
        # 跨年退货标记：共用写账规则出、CC 认，键同名（B3b 实录点 ②）
        self.assertTrue(quoted((RULES / "sales_mapper.py").read_text(encoding="utf-8"), "cross_year"))
        self.assertTrue(quoted((COMPANION / "sales_adapter.py").read_text(encoding="utf-8"), "cross_year"))
        # 免税行标记：ERP 合同、共用写账规则、CC 三处同名
        for path in (erp / "contracts.py", RULES / "common.py", COMPANION / "dbf_fields.py"):
            self.assertTrue(quoted(path.read_text(encoding="utf-8"), "vat_exempt"), path)

    def test_the_pos_block_keys_match(self):
        mapper = (RULES / "sales_mapper.py").read_text(encoding="utf-8")
        adapter = (COMPANION / "sales_adapter.py").read_text(encoding="utf-8")
        for key in ("first", "last", "count", "docnum", "cash", "transfers", "acc", "amount", "label"):
            self.assertTrue(quoted(mapper, key) and quoted(adapter, key), key)
        # 票号起止的长度上限两边一致（云端截短，小助手的校验永远能过）
        self.assertEqual(_literal(ROOT / "apps/pearnly_core/pearnly_core/express_rules/contracts/pos.py", "NUMBER_MAX"),
                         _literal(COMPANION / "sales_adapter.py", "_POS_NUMBER_MAX"))
        self.assertIn('"auto"', mapper)
        self.assertIn('"auto"', adapter)

    def test_the_helper_refusals_are_for_a_person_and_translated(self):
        constants = module_constants(COMPANION / "pos_journal.py")
        lifecycle = (WIRE / "ack_rules.py").read_text(encoding="utf-8")  # 转人工的错误码表（ADR-025 搬进共用层）
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


class AnyAccountSetRead(unittest.TestCase):
    """云端按需请电脑读它没选的那份账套（2026-10-13）：服务端 `connections/refresh.py`（租）、`connections/reports.py`
    （收）↔ 小助手 `projection_refresh.py`（读与回执）、`worker._run_periodic`（心跳）。"""

    CONNECTIONS = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "connections"

    def test_this_helper_reads_any_account_set_the_server_hands_it(self):
        """服务端只把电脑没选的账套交给 `ANY_ACCOUNT_SET_MIN_VERSION` 及以上：仓库里这版不能低于它。"""
        version = module_constants(COMPANION / "version.py")["VERSION"]
        minimum = module_constants(self.CONNECTIONS / "refresh.py")["ANY_ACCOUNT_SET_MIN_VERSION"]
        self.assertGreaterEqual(tuple(int(part) for part in version.split(".")),
                                tuple(int(part) for part in minimum.split(".")))

    def test_the_lease_command_has_the_keys_the_helper_reads(self):
        server = _literal(WIRE / "queue_contracts.py", "_REFRESH_KEYS")
        command = (COMPANION / "projection_refresh.py").read_text(encoding="utf-8")
        reads = set(re.findall(r'pending\.get\("([a-z_]+)"\)', command))
        self.assertEqual(reads, set(server))
        # 多台电脑（ADR-021）：交给电脑的账套键是它自己的写法（同一份账套在各台电脑上的路径可能不同），键名不变
        source = (self.CONNECTIONS / "refresh.py").read_text(encoding="utf-8")
        self.assertIn("'account_set_key': wire_key, 'scope_kind': row.scope_kind", source)
        self.assertIn("'account_set_key': child.account_set_key, 'scope_kind': 'endpoint'", source)

    def test_the_server_reads_the_refresh_receipt_and_version_the_helper_sends(self):
        helper = (COMPANION / "projection_refresh.py").read_text(encoding="utf-8")
        server = (self.CONNECTIONS / "reports.py").read_text(encoding="utf-8")
        for key in ("master_refresh_request_id", "master_refresh_scope", "master_refresh_account_set",
                    "master_refresh_error"):
            self.assertTrue(quoted(helper, key) and quoted(server, key), key)
        # 能不能交别的账套看心跳报的版本：每一拍都报
        self.assertIn("companion_version=VERSION", (COMPANION / "worker.py").read_text(encoding="utf-8"))
        self.assertTrue(quoted(server, "companion_version"))
        # 能不能交别的账套看来领的这台电脑报的版本（第 2 批起每台各自一份，`companion.devices.member`）
        self.assertIn("reads_any_account_set(member.version)", (self.CONNECTIONS / "refresh.py").read_text(encoding="utf-8"))

    def test_reading_follows_the_write_rules_and_never_changes_the_selection(self):
        source = method_source_of(COMPANION / "projection_refresh.py", "collect_account")
        self.assertIn("item.get('writable') is not False", source)
        self.assertIn("_under(item, roots)", source)
        self.assertNotIn("cfg.update", source)
        periodic = method_source_of(COMPANION / "worker.py", "_run_periodic")
        self.assertIn("roots=[root or '', *authorized]", periodic)


class AccountSetIdentity(unittest.TestCase):
    """多台电脑认同一份账套（ADR-021，方案 2.3）：CC 桌面版 1.7.0（第 5 批）在每行账套上加报 ``uid``（目录文件号）与
    ``share``（网络路径）。服务端两处白名单（原始名单 `_ACCOUNT_SET_KEYS`、目录属性 `attribute_keys`）都要收；
    客户端报的账套行键都在服务端白名单里；心跳与配对上报走同一个 `with_identity`。"""

    REPORTS = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "connections" / "reports.py"
    # 目录属性的白名单在共用层的净化规则里（ADR-025）
    RULES = WIRE / "report_rules.py"

    def server_keys(self):
        raw = attribute = None
        for node in [*ast.walk(ast.parse(self.REPORTS.read_text(encoding="utf-8"))),
                     *ast.walk(ast.parse(self.RULES.read_text(encoding="utf-8")))]:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_ACCOUNT_SET_KEYS":
                raw = set(ast.literal_eval(node.value))
            if (isinstance(node, ast.FunctionDef) and node.name == "account_sets"):
                for keyword in ast.walk(node):
                    if isinstance(keyword, ast.keyword) and keyword.arg == "attribute_keys":
                        attribute = set(ast.literal_eval(keyword.value))
        return raw, attribute

    def test_both_server_whitelists_take_uid_and_share(self):
        raw, attribute = self.server_keys()
        self.assertTrue({"uid", "share"} <= raw)
        self.assertTrue({"uid", "share"} <= attribute)

    def test_every_account_set_key_the_helper_reports_today_is_whitelisted(self):
        probe = (COMPANION / "account_probe.py").read_text(encoding="utf-8")
        tree = ast.parse(probe)
        fields = set()
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and node.name == "AccountSet":
                fields = {item.target.id for item in node.body if isinstance(item, ast.AnnAssign)}
        self.assertTrue(fields)
        identity = set(module_constants(COMPANION / "account_probe.py")["IDENTITY_KEYS"])
        self.assertEqual(identity, {"uid", "share"})
        entry = module_constants(COMPANION / "account_probe.py")["ENTRY_KEY"]
        reported = fields | {"root", "root_label", "mapping", entry} | identity
        raw, attribute = self.server_keys()
        # 原始名单清洗按白名单逐键取（`mapping` 单独处理）；目录属性另收期间与分店
        self.assertEqual(reported - raw - {"mapping"}, set())
        self.assertEqual((reported - {"name"}) - attribute, set())

    def test_the_entry_mark_is_one_key_both_sides_and_stays_out_of_the_directory(self):
        """第 3 期 E3（1.9.1）：账套行另报的录入标记（GLJNL.DBF 表头 ``YYYY-MM-DD/记录数``）同名进两处白名单、池登记按同一个键
        读、不进池目录；服务端认的写法就是桌面端写出的样子；桌面端只读表头前 8 个字节（不经 dbf 库打开整张表）。"""
        helper = module_constants(COMPANION / "account_probe.py")["ENTRY_KEY"]
        self.assertEqual(helper, "entry_mark")
        self.assertEqual(module_constants(ROOT / "apps/pearnly_cowork/pearnly_cowork/connections/pool.py")["ENTRY_KEY"], helper)
        raw, attribute = self.server_keys()
        self.assertIn(helper, raw)
        self.assertIn(helper, attribute)
        reader = method_source_of(COMPANION / "account_probe.py", "read_entry_header")
        self.assertIn("stream.read(8)", reader)
        self.assertNotIn("dbf.Table", reader)
        self.assertIn("int.from_bytes(head[4:8], 'little')", reader)
        self.assertIn("day.isoformat()}/", reader)
        policy = (ROOT / "apps/pearnly_cowork/pearnly_cowork/contracts/document_policy.py").read_text(encoding="utf-8")
        self.assertIn('_ENTRY = re.compile(r"(\\d{4}-\\d{2}-\\d{2})/(\\d{1,10})")', policy)
        payload = method_source_of(COMPANION / "account_probe.py", "account_sets_payload")
        self.assertIn("row[ENTRY_KEY] = mark", payload)

    def test_the_helper_fills_uid_and_share_on_every_row_it_reports(self):
        fill = method_source_of(COMPANION / "account_probe.py", "with_identity")
        for key in ("uid", "share"):
            self.assertIn(f"row['{key}']", fill)
        self.assertIn("with_identity(rows, locator)", method_source_of(COMPANION / "account_probe.py",
                                                                      "account_sets_payload"))
        self.assertIn("with_identity([asdict(a) for a in sets])", (COMPANION / "pairing.py").read_text(encoding="utf-8"))
        # 文件号只用 GetFileInformationByHandle（卷序列号 + 64 位文件号），不用 FileIdInfo（第 0 批：NAS / 服务器共享取不到）
        facts = (COMPANION / "device_facts.py").read_text(encoding="utf-8")
        self.assertIn("GetFileInformationByHandle", facts)
        self.assertNotIn("GetFileInformationByHandleEx", facts)
        self.assertIn("_BACKUP_SEMANTICS = 0x02000000", facts)
        self.assertIn("_FILE_READ_ATTRIBUTES = 0x0080", facts)
        clean = (ROOT / "apps/pearnly_core/pearnly_core/express_rules/targets/identity.py").read_text(encoding="utf-8")
        self.assertIn('ch.isalnum() or ch in ":-_."', clean)   # uid_of 的写法（十六进制 + 连字符）在服务端清洗规则内


class WriteDispatch(unittest.TestCase):
    """多台电脑分派写账（ADR-021，方案 2.5、第 5 节第 4–6 条，服务端一侧；客户端一侧在第 5 批补齐）：

    - 领取回复的每件任务：四个旧键照旧必有，可选键只有登记过的 ``attended`` 与 ``task_key``（ADR-021 11-02 修订：同一条
      推送记录每次领都一样的不透明键，本机记账按它认）；旧客户端只按键取它认识的，多出的忽略；
    - 领取请求的可选键 ``index_pending``：服务端认它，旧服务端忽略；
    - 写前文件号对不上回 ``waiting_lock`` + ``[ERR_ACCOUNT_UID_MISMATCH]``：服务端据此标 stale；
    - 下发副本里换成领取电脑写法的位置字段，覆盖客户端 `_task_settings` 读的每一个位置键。"""

    QUEUE = WIRE / "queue_contracts.py"
    LOCATION = ROOT / "apps" / "pearnly_core" / "pearnly_core" / "express_rules" / "push" / "wire_location.py"

    def constants(self, path, *names):
        found = {}
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") in names:
                found[node.targets[0].id] = ast.literal_eval(node.value.args[0] if isinstance(node.value, ast.Call)
                                                             else node.value)
        return found

    def test_jobs_keep_the_four_keys_and_register_the_optional_ones(self):
        found = self.constants(self.QUEUE, "_JOB_KEYS", "JOB_OPTIONAL_KEYS", "LEASE_OPTIONAL_KEYS")
        self.assertEqual(set(found["_JOB_KEYS"]), {"log_id", "history_id", "invoice_no", "payload"})
        self.assertEqual(set(found["JOB_OPTIONAL_KEYS"]), {"attended", "task_key"})
        self.assertEqual(set(found["LEASE_OPTIONAL_KEYS"]), {"index_pending"})
        poll = method_source(COMPANION / "queue_client.py", "QueueClient", "poll")
        read = set(re.findall(r"""job(?:\.get\(|\[)['"]([a-z_]+)['"]""", poll))
        # 1.7.0：按键取三个旧键 + 可选的 attended（只认布尔，没有按有人值守）；多出的键不碍事。1.12.11：再取可选的
        # task_key（只认非空字符串，没有按投递号记，同以前）
        self.assertEqual(read, {"log_id", "invoice_no", "payload", "attended", "task_key"})
        self.assertTrue(read <= set(found["_JOB_KEYS"]) | set(found["JOB_OPTIONAL_KEYS"]))
        self.assertIn("attended if isinstance(attended, bool) else None", poll)
        self.assertIn("key=str(key) if isinstance(key, str) and key else None", poll)
        # 本机记账、「终止」都按 task_journal.key_of 取键：有稳定键用它，旧服务端没有就用投递号
        key_of = method_source_of(COMPANION / "task_journal.py", "key_of")
        self.assertIn("getattr(task, 'key', None) or task.id", key_of)

    def test_the_stable_key_is_opaque_and_the_same_for_one_record(self):
        sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
        from pearnly_core.cc_desktop.wire import queue_contracts
        key = queue_contracts.task_key("CWP-abc")
        self.assertEqual(key, queue_contracts.task_key("CWP-abc"))
        self.assertNotEqual(key, queue_contracts.task_key("CWP-abd"))
        self.assertNotIn("CWP", key)
        job = queue_contracts.job("1b4e28ba-2fa1-11d2-883f-0016d3cca427", {"history": "H", "frozen_payload": {}},
                                  None, key)
        self.assertEqual(job["task_key"], key)
        # 旧服务端回的任务没有这一键：今天的客户端照旧按投递号
        self.assertNotIn("task_key", queue_contracts.job("1b4e28ba-2fa1-11d2-883f-0016d3cca427",
                                                         {"history": "H", "frozen_payload": {}}))

    def test_every_lease_request_carries_index_pending_within_the_server_limit(self):
        poll = method_source(COMPANION / "queue_client.py", "QueueClient", "poll")
        sent = next(node for node in ast.walk(ast.parse(poll)) if isinstance(node, ast.Dict)
                    and any(getattr(key, "value", None) == "max" for key in node.keys))
        self.assertEqual({key.value for key in sent.keys}, {"max", "agent_id", "index_pending"})
        self.assertEqual(number_constants(COMPANION / "queue_client.py", ("MAX_INDEX_PENDING",)),
                         number_constants(self.QUEUE, ("MAX_INDEX_PENDING",)))
        # 报的是账套目录的文件号（与心跳账套行的 uid 同一个函数），全部成员合起来
        scheduler = (COMPANION / "multi_profile_worker.py").read_text(encoding="utf-8")
        self.assertIn("uid_of: Callable[[str], str] = device_facts.cached_uid", scheduler)
        self.assertIn("qc.poll(index_pending=self.index_pending())", scheduler)
        self.assertIn("value = file_uid(path, api)", method_source_of(COMPANION / "device_facts.py", "cached_uid"))

    def test_the_uid_mismatch_code_is_named_once_and_acted_on(self):
        self.assertEqual(module_constants(self.QUEUE)["ERR_ACCOUNT_UID_MISMATCH"], "ERR_ACCOUNT_UID_MISMATCH")
        self.assertEqual(module_constants(COMPANION / "worker.py")["ERR_ACCOUNT_UID_MISMATCH"], "ERR_ACCOUNT_UID_MISMATCH")
        handler = method_source_of(COMPANION / "worker.py", "_handle_dbf_task")
        check = handler.split("if expected_uid and", 1)[1].split("return 'waiting_lock'", 1)[0]
        self.assertIn("error_code=ERR_ACCOUNT_UID_MISMATCH", check)
        self.assertIn("outcome='waiting_lock'", check)
        self.assertIn("payload.get('account_uid')", handler)   # 服务端下发的键（push.express.wire_location）
        self.assertIn('"account_uid"', (ROOT / "apps/pearnly_core/pearnly_core/express_rules/push/wire_location.py")
                      .read_text(encoding="utf-8").replace("'", '"'))
        transport = (SERVER / "transport.py").read_text(encoding="utf-8")
        self.assertIn("queue_contracts.ERR_ACCOUNT_UID_MISMATCH", transport)
        self.assertIn("mark_stale(", transport)
        self.assertIn("request_full_report(", transport)

    def test_a_task_written_here_is_never_handed_back_to_the_queue(self):
        # 第 5 批审查：这台写过（回执没送到）的单先于占用探测、终止与文件号核对认出来，照上次回执回报；否则放回排队会交给
        # 没有本机写过记录的电脑再写一遍（服务端只按任务号去重不了 Express 里已建的单）
        # 11-13 起取任务线程不再自己探占用、不弹询问框（方案甲），写过与否、占用都在 _handle_dbf_task 里判
        handle = method_source(COMPANION / "gui_tray_workers.py", "PollWorker", "handle_task")
        self.assertNotIn("is_account_busy", handle)
        self.assertNotIn("_abort_task", handle)
        handler = method_source_of(COMPANION / "worker.py", "_handle_dbf_task")
        self.assertLess(handler.index("task_journal.written(task_journal.key_of(task))"), handler.index("if expected_uid and"))

    def test_the_substituted_location_covers_what_the_helper_reads(self):
        keys = set(ast.literal_eval(next(
            node.value for node in ast.parse(self.LOCATION.read_text(encoding="utf-8")).body
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "LOCATION_KEYS")))
        settings = method_source_of(COMPANION / "worker.py", "_task_settings")
        read = set(re.findall(r"""payload or \{\}\)\.get\(['"]([a-z_]+)['"]\)""", settings))
        self.assertEqual(read, {"account_set", "account_root"})
        self.assertTrue(read <= keys)


class PoolRequests(unittest.TestCase):
    """多台电脑的按需请求（ADR-021 第 4 批，方案 2.7、第 5 节「按需请求领取」与第 6 条）：

    - 领取的请求体不改（``{version, kinds, device}``，电脑是谁由密钥推出），客户端不用改；
    - 读账套的两种（回读、科目历史）领取时把通用位置键换成领取那台电脑的写法：这组键 = 服务端两种读账参数都有的键
      = 客户端两个处理函数读的位置键；读文件夹的定向给持有根目录的电脑，参数照旧。"""

    READBACK = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "readback"

    def returned_keys(self, path, function):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        builder = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function)
        return {key.value for key in next(node.value for node in ast.walk(builder) if isinstance(node, ast.Return)).keys}

    def test_the_substituted_location_keys_are_what_both_reads_send_and_the_helper_reads(self):
        server = number_constants(WIRE / "request_contracts.py", ("LOCATION_KEYS", "ACCOUNT_KINDS", "FOLDER_KINDS",
                                                                     "KINDS"))
        self.assertEqual(set(server["LOCATION_KEYS"]), {"account_set", "account_root"})
        self.assertEqual(set(server["ACCOUNT_KINDS"]), {"express_read", "account_history", "express_masters", "express_marks",
                                                       "express_doc_check", "express_ar_open"})
        self.assertEqual(set(server["ACCOUNT_KINDS"]) | set(server["FOLDER_KINDS"]), set(server["KINDS"]))
        for path, function in ((self.READBACK / "contracts.py", "read_params"),
                               (self.READBACK / "history_rules.py", "read_params")):
            self.assertTrue(set(server["LOCATION_KEYS"]) <= self.returned_keys(path, function), path)
        for path, function in ((COMPANION / "express_read.py", "read_books"),
                               (COMPANION / "account_history.py", "read_history")):
            source = method_source_of(path, function)
            self.assertIn("resolve(cfg, params.get('account_set'), params.get('account_root'))", source)

    def test_the_lease_body_is_unchanged_and_the_server_targets_by_key_and_computer(self):
        round_source = method_source(COMPANION / "request_worker.py", "RequestWorker", "round")
        sent = next(node for node in ast.walk(ast.parse(round_source)) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "client.next")
        self.assertEqual({key.value for key in sent.args[0].keys}, {"version", "kinds", "device"})
        service = (SERVER / "request_service.py").read_text(encoding="utf-8")
        self.assertIn("device = credentials.device_id(context.credential_id)", service)
        self.assertIn("IFNULL(target_device, '') IN (%s, '')", service)
        self.assertIn("params = contracts.localize(params, reach.path, reach.root)", service)
        fetch = (FOLDER_SERVER / "fetch.py").read_text(encoding="utf-8")
        self.assertIn("target_device=device", fetch)
        for path in (self.READBACK / "service.py", self.READBACK / "history.py"):
            self.assertIn('account_key=', path.read_text(encoding="utf-8"))


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UpdateBlocked(unittest.TestCase):
    """自动更新被 Windows 拦下（教程问题第 1 条，2026-10-15）：CC 桌面版 `update_block` 记下并在心跳、文件夹同步里报
    ``update_blocked``（版本 + UTC 时刻，没被拦是 null，旧版不带）；服务端 `connections/desktop_update` 收（心跳
    `reports.plan_report`、同步 `folder_intake.machine`），网页「连接」如实显示一句；桌面端设置页给「下载新版」。"""

    CONNECTIONS = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "connections"

    @classmethod
    def setUpClass(cls):
        # CC 桌面版这边只执行 update_block 与 version 两个纯模块（config、logger 用到时才导入，CI 的 Python 没装桌面端依赖）
        sys.path.insert(0, str(ROOT / "companion" / "src"))
        try:
            cls.helper = importlib.import_module("companion.update_block")
        finally:
            sys.path.remove(str(ROOT / "companion" / "src"))
        cls.server = _module("contract_desktop_update", cls.CONNECTIONS / "desktop_update.py")

    def test_what_the_helper_reports_is_what_the_server_stores(self):
        at = 1_791_890_280.0   # 2026-10-13 11:18 UTC（曼谷 18:18）
        record = self.helper.Record("1.5.2", "blocked", at, 1, 4551)
        sent = self.helper.report_of(record)
        self.assertEqual(sent, {"version": "1.5.2", "at": "2026-10-13T11:18:00Z"})
        self.assertEqual(self.server.parse(sent), {"version": "1.5.2", "at": "2026-10-13T11:18:00+00:00"})
        for state in ("launched", "failed"):   # 别的没装上不说成被拦
            self.assertIsNone(self.helper.report_of(self.helper.Record("1.5.2", state, at)))
        self.assertIsNone(self.server.parse(None))
        self.assertEqual(self.server.KEY, "update_blocked")
        # 只有系统按策略拒绝运行才算被拦（4551 应用控制 / 智能应用控制）；找不到文件、拒绝访问不算
        self.assertIn(4551, self.helper.BLOCKED_ERRORS)
        self.assertFalse({2, 5} & set(self.helper.BLOCKED_ERRORS))

    def test_heartbeat_and_folder_sync_both_carry_it_and_the_server_reads_both(self):
        self.assertIn("update_blocked=update_block.reported()", method_source_of(COMPANION / "worker.py", "_run_periodic"))
        self.assertIn('body["update_blocked"] = update_blocked', (COMPANION / "queue_client.py").read_text(encoding="utf-8"))
        round_source = method_source(COMPANION / "folder_worker.py", "FolderIntake", "round")
        sync = next(node for node in ast.walk(ast.parse(round_source)) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "client.sync")
        self.assertIn("update_blocked", {key.value for key in sync.args[0].keys})
        reports = (self.CONNECTIONS / "reports.py").read_text(encoding="utf-8")
        self.assertIn("if desktop_update.KEY in body:", reports)    # 旧版不带：保留上次的值
        self.assertRegex(reports, r"CACHE_KEYS = frozenset\(\([^)]*desktop_update\.KEY,")
        machine = (FOLDER_SERVER / "machine.py").read_text(encoding="utf-8")
        self.assertIn("update_blocked=desktop_update.parse(body.get(desktop_update.KEY))", machine)

    def test_the_web_line_shows_only_while_it_is_still_true(self):
        blocked = {"version": "1.5.2", "at": "2026-10-13T11:18:00Z"}
        self.assertEqual(self.server.current(blocked, "1.5.1")["version"], "1.5.2")
        self.assertIsNone(self.server.current(blocked, "1.5.2"))   # 已手动装上
        self.assertIsNone(self.server.current(blocked, "1.10.0"))
        self.assertIsNone(self.server.current(None, "1.5.1"))
        rules = (FOLDER_SERVER / "rules.py").read_text(encoding="utf-8")
        self.assertIn('row["update_blocked"] = view["update_blocked"]', rules)
        page = (ROOT / "frontend/assistant/src/components/settings.tsx").read_text(encoding="utf-8")
        self.assertIn("row.update_blocked && (", page)
        self.assertIn("update_blocked?: { version: string; at: string } | null;", page)

    def test_download_new_version_opens_the_same_installer_as_the_website(self):
        updater = module_constants(COMPANION / "updater.py")
        release = module_constants(ROOT / "apps/pearnly_core/pearnly_core/cc_desktop/release.py")
        website = module_constants(ROOT / "website" / "content.py")
        self.assertEqual(updater["DOWNLOAD_PAGE"], release["INSTALLER_URL"])
        self.assertEqual(updater["DOWNLOAD_PAGE"], updater["DOWNLOAD_SITE"] + website["COMPANION_URL"])

    def test_the_settings_page_and_the_desktop_agree_on_the_state_and_the_button(self):
        page = (ROOT / "frontend/assistant/src/components/settings.tsx").read_text(encoding="utf-8")
        self.assertIn('onDownload={() => void run("download", "download_update")}', page)
        self.assertIn('const blocked = updateState === "blocked";', page)
        self.assertIn('if method == "download_update":', (COMPANION / "tomo_bridge.py").read_text(encoding="utf-8"))
        flow = (COMPANION / "auto_update.py").read_text(encoding="utf-8")
        self.assertIn('return "blocked", version', flow)


class CompanyFolders(unittest.TestCase):
    """CC 按公司分组、文件夹授权随公司走（2026-10-15，规格 §7、§13.2）：

    - 文件夹同步：快速同步的请求键 `roots_only`、回复键 `max_roots` / `roots_in_use` 两端都有；一次收几个根目录
      （`MAX_ROOTS`）与下级文件夹名合计上限（`FOLDERS_TOTAL`）两端相等；
    - 桥：对话页调的每个本机方法桥里都有；公司的「选择文件夹…」叫 `pick_folder`，桥在 `state().features` 里声明
      `company_folders`，设置页的旧授权清理行读 `state().legacy_roots`。"""

    KEYS = ("roots_only", "max_roots", "roots_in_use")

    def setUp(self):
        self.worker = (COMPANION / "folder_worker.py").read_text(encoding="utf-8")
        self.server = "\n".join(path.read_text(encoding="utf-8") for path in sorted(FOLDER_SERVER.glob("*.py")))
        self.bridge = (COMPANION / "tomo_bridge.py").read_text(encoding="utf-8")
        self.page = "\n".join(path.read_text(encoding="utf-8")
                              for path in sorted((ROOT / "frontend/assistant/src").rglob("*.ts*")))

    def test_the_desktop_sends_and_reads_the_sync_keys(self):
        quick = method_source(COMPANION / "folder_worker.py", "FolderIntake", "report_roots_now")
        sync = next(node for node in ast.walk(ast.parse(quick)) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "client.sync")
        self.assertTrue({"roots_only", "version", "device", "roots"} <= {key.value for key in sync.args[0].keys})
        limits = method_source(COMPANION / "folder_worker.py", "FolderIntake", "_limits")
        self.assertIn("response.get('max_roots')", limits)
        self.assertIn("response.get('roots_in_use')", limits)
        self.assertIn("self._limits(key, response)", method_source(COMPANION / "folder_worker.py", "FolderIntake", "round"))

    def test_a_binding_names_the_computer_the_same_way_as_the_sync(self):
        """第 4 批审查 M2：几台电脑报了同一个根目录编号（都映射成 S 盘）时，服务端按绑定里的电脑名认是哪台；这个名字与
        文件夹同步报的 `device` 出自同一个函数（`queue_client._device_name`），服务端存同步与读绑定都过 `contracts.device`。"""
        pick = method_source(COMPANION / "desktop_settings.py", "DesktopSettings", "pick_folder")
        self.assertIn("'device': _device_name()", pick)  # method_source 是 ast.unparse 过的（单引号）
        worker = (COMPANION / "folder_worker.py").read_text(encoding="utf-8")
        self.assertEqual(worker.count('"device": _device_name()'), 2)  # 完整同步与快速同步
        bind = (FOLDER_SERVER / "company_folders.py").read_text(encoding="utf-8")
        self.assertIn('name = contracts.device(binding.get("device"))', bind)
        self.assertIn("name=name)", bind)
        machine = (FOLDER_SERVER / "machine.py").read_text(encoding="utf-8")
        self.assertIn('device=contracts.device(body.get("device"))', machine)

    def test_the_server_reads_and_answers_the_sync_keys(self):
        for key in self.KEYS:
            self.assertTrue(quoted(self.server, key), key)

    def test_root_and_folder_limits_are_the_same_on_both_sides(self):
        names = ("MAX_ROOTS", "FOLDERS_TOTAL")
        helper = number_constants(COMPANION / "folder_plan.py", names)
        server = number_constants(FOLDER_SERVER / "contracts.py", names)
        self.assertEqual(helper, {"MAX_ROOTS": 200, "FOLDERS_TOTAL": 20_000})
        self.assertEqual(server, helper)
        # 没收到 max_roots（旧服务端）时桌面版按旧上限 20 封顶
        self.assertEqual(number_constants(COMPANION / "folder_plan.py", ("DEFAULT_MAX_ROOTS",)), {"DEFAULT_MAX_ROOTS": 20})

    def test_every_method_the_page_calls_is_in_the_bridge(self):
        bridge = set(re.findall(r'method == "(\w+)"', self.bridge))
        called = set(re.findall(r'\.call\(\s*"(\w+)"', self.page)) | set(re.findall(r'\brun\(\s*"\w+",\s*"(\w+)"', self.page))
        self.assertTrue(called, "frontend bridge calls")
        self.assertEqual(called - bridge, set())
        self.assertTrue({"pick_folder", "remove_folder", "add_folder"} <= bridge)
        # 「CC 开着时保持电脑唤醒」「用电池时也保持」（ADR-021 第 58 条）：页面按字面量调，桥分别改两个开关
        self.assertTrue({"keep_awake", "keep_awake_on_battery"} <= called)
        self.assertIn('if method == "keep_awake":\n            return self._changed(s.set_keep_awake(enabled=', self.bridge)
        self.assertIn('if method == "keep_awake_on_battery":\n            return self._changed(s.set_keep_awake(on_battery=',
                      self.bridge)

    def test_the_page_uses_the_company_folder_bridge(self):
        # 由前端包（frontend/assistant）接上：pick_folder、features 里的 company_folders、legacy_roots
        self.assertIn('FEATURES = ("company_folders", "devices", "sites", "express_places", "operator", "mrerp_login", "place_channels")', self.bridge)
        self.assertIn('"legacy_roots"', (COMPANION / "desktop_settings.py").read_text(encoding="utf-8"))
        for name in ("pick_folder", "company_folders", "legacy_roots"):
            self.assertTrue(quoted(self.page, name) or re.search(r"\b%s\b" % name, self.page), name)



class ErpExpressFront(unittest.TestCase):
    """ERP 当手机前台、Express 当账本（2026-10-22）：CC 桌面版这一侧的两项约定。

    - 按需请求 ``express_masters``：读一本账套（按规范键分派、参数里的位置换成领取电脑的写法）、结果属于连接与账套
      （发起人是 Cowork 的网关服务账号，没在任何电脑上登录过 CC）；参数只有两个位置键；要 ``KIND_MIN_COMPANION`` 起的 CC；
    - 载荷 4：明细行 / 表头可带 ``loccod``，小助手认 4；仓库码不在 ISTAB 21 里整单不写，回 ``ERR_LOCATION``，按「要人处理」回报。"""

    def test_the_masters_request_is_an_account_read_shared_with_the_connection(self):
        server = number_constants(WIRE / "request_contracts.py", ("KINDS", "ACCOUNT_KINDS", "SHARED_KINDS",
                                                                     "FOLDER_KINDS", "LOCATION_KEYS",
                                                                     "KIND_MIN_COMPANION", "MAX_BYTES"))
        for group in ("KINDS", "ACCOUNT_KINDS", "SHARED_KINDS"):
            self.assertIn("express_masters", server[group], group)
        self.assertNotIn("express_masters", server["FOLDER_KINDS"])  # MR.ERP 的文件夹成员不读账套
        self.assertEqual(server["MAX_BYTES"]["express_masters"], 16 * 1024 * 1024)
        helper = number_constants(COMPANION / "express_masters.py", ("PARAM_KEYS",))["PARAM_KEYS"]
        self.assertEqual(set(helper), set(server["LOCATION_KEYS"]))
        self.assertIn("express_read.resolve(cfg, account_set, account_root)",
                      method_source_of(COMPANION / "express_masters.py", "read_masters"))
        self.assertIn('"express_masters": express_masters.read_masters',
                      (COMPANION / "request_worker.py").read_text(encoding="utf-8"))
        version = module_constants(COMPANION / "version.py")["VERSION"]
        minimum = server["KIND_MIN_COMPANION"]["express_masters"]
        self.assertGreaterEqual(tuple(map(int, version.split("."))), tuple(map(int, minimum.split("."))))
        self.assertIn("def minimum(", (WIRE / "request_contracts.py").read_text(encoding="utf-8"))

    def test_the_masters_reader_never_waits_for_the_account_set_lock(self):
        tree = ast.parse((COMPANION / "express_masters.py").read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
                    for alias in node.names} | {node.module or "" for node in ast.walk(tree)
                                                if isinstance(node, ast.ImportFrom)}
        self.assertFalse({"account_lock", "is_account_busy", "dbf_writer"} & imported)

    def test_payload_4_names_the_warehouse_and_a_wrong_code_needs_a_person(self):
        self.assertIn(4, _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS"))
        self.assertEqual(module_constants(COMPANION / "stock_location.py")["ERR_LOCATION"], "ERR_LOCATION")
        outcome = method_source_of(COMPANION / "worker.py", "_outcome_for")
        self.assertIn("ERR_LOCATION: 'needs_review'", outcome)
        for writer in ("dbf_writer.py", "dbf_sales.py"):
            source = (COMPANION / writer).read_text(encoding="utf-8")
            self.assertIn("stock_location.plan(account_dir, payload)", source, writer)
            self.assertIn("error_code=ERR_LOCATION", source, writer)


class ServiceVatBasis(unittest.TestCase):
    """服务品进项税口径（10-29）：CC 桌面版的规则副本（`companion/service_vat.py`）与共用写账规则逐例一致；拒写码、载荷键、
    选项编码、载荷版本两边一致，拒写码云端认作「要人处理」、CC 归为 needs_review、Cowork 有译文。"""

    NAMES = ("verdict", "book_verdict", "chosen_value", "basis")

    def _rules(self, path):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        keep = [node for node in tree.body
                if (isinstance(node, ast.FunctionDef) and node.name in self.NAMES)
                or (isinstance(node, ast.Assign) and any(getattr(t, "id", "") in
                                                        ("ACCRUAL", "CASH_BASIS", "NO_VAT", "VALUES", "OPTIONS")
                                                        for t in node.targets))]
        from typing import Iterable, Optional
        namespace = {"Iterable": Iterable, "Optional": Optional}
        exec(compile(ast.Module(body=keep, type_ignores=[]), str(path), "exec"), namespace)
        return namespace

    def test_the_rule_copy_matches_the_shared_rule(self):
        shared, copy = self._rules(RULES / "service_vat.py"), self._rules(COMPANION / "service_vat.py")
        histories = ("", "0", "1", "2", "12", "21")
        for has_vat in (True, False):
            for cash in (True, False):
                for service in (True, False):
                    for supplier in histories:
                        for book in histories:
                            for chosen in ("", "accrual", "cash_basis", "1", "2", "x"):
                                args = dict(has_vat=has_vat, cash=cash, service=service, supplier=supplier, book=book,
                                            chosen=chosen)
                                self.assertEqual(shared["basis"](**args), copy["basis"](**args), args)
        for values in (["1", "0", "2"], ["2", "2"], [], ["12"]):
            self.assertEqual(shared["book_verdict"](values), copy["book_verdict"](values))

    def test_codes_keys_and_versions_agree(self):
        shared, copy = module_constants(RULES / "service_vat.py"), module_constants(COMPANION / "service_vat.py")
        for name in ("CHOICE_CODE", "PAYLOAD_KEY", "OPTIONS", "ACCRUAL", "CASH_BASIS", "NO_VAT"):
            self.assertEqual(shared[name], copy[name], name)
        self.assertIn(_literal(RULES / "service_vat.py", "PAYLOAD_VERSION"),
                      _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS"))
        code = shared["CHOICE_CODE"]
        self.assertIn(f'"{code}"', (WIRE / "ack_rules.py").read_text(encoding="utf-8"))
        self.assertIn(f'"{code}": "needs_review"', (COMPANION / "worker.py").read_text(encoding="utf-8"))
        self.assertIn(f"intake.error.{code}", (ROOT / "apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv")
                      .read_text(encoding="utf-8"))
        # 供应商主档带口径串 srv：CC 两处上报（心跳目录、ERP 主档读取），服务端净化时留下这一键
        self.assertIn('"srv"', (COMPANION / "catalog_probe.py").read_text(encoding="utf-8"))
        self.assertIn('"srv"', (COMPANION / "express_masters.py").read_text(encoding="utf-8"))
        self.assertIn("srv", module_constants(WIRE / "report_rules.py")["SUPPLIER_KEYS"])



class CatalogWarehouses(unittest.TestCase):
    """仓库表随心跳目录报（CC 桌面版 1.12.18，11-08 Cowork 推单选 Express 仓库）：CC 报的键 = 服务端净化留下的键；
    主仓 / 在用用 "1" 标（服务端只认 "1"）；代码表段与 CC 写入校验的仓库表同一段（ISTAB 21）。"""

    def test_the_catalog_reports_warehouses_with_the_keys_the_server_keeps(self):
        probe = (COMPANION / "catalog_probe.py").read_text(encoding="utf-8")
        self.assertIn('"warehouses": warehouses', probe)
        for key in module_constants(WIRE / "report_rules.py")["WAREHOUSE_KEYS"]:
            self.assertIn(f'"{key}"', probe, key)
        self.assertIn('row["main"] = "1"', probe)
        self.assertIn('row["used"] = "1"', probe)
        rules = (RULES / "choices.py").read_text(encoding="utf-8")
        self.assertIn('str(row.get("used") or "") == "1"', rules)
        self.assertIn('str(row.get("main") or "") == "1"', rules)
        self.assertEqual(module_constants(COMPANION / "catalog_probe.py")["_WAREHOUSE_TABTYP"],
                         module_constants(COMPANION / "stock_location.py")["WAREHOUSE_TABTYP"])
        self.assertIn('("warehouses", _WAREHOUSE_KEYS)',
                      (ROOT / "apps/pearnly_cowork/pearnly_cowork/connections/reports.py").read_text(encoding="utf-8"))


class OperatorReceiptDetail(unittest.TestCase):
    """代录回执（写账回执 ``ack_failure``，CC 桌面版 1.12.15 起）：说明（detail）以 AI 接手的结论原文开头
    （``ai: <结论> · <步骤>: <固定流程问题>``）、最多 2000 字，**不再以截图路径开头**；转人工那一刻的截图本机路径放在
    ``meta.operator.evidence``（字符串，站点单独处理）。旧服务端对说明长度有上限的自行截断，不影响 CC。"""

    def test_detail_leads_with_the_ai_conclusion_and_the_screenshot_rides_in_meta_evidence(self):
        op = COMPANION / "express_operator"
        self.assertEqual(_literal(op / "contract.py", "DETAIL_MAX"), 2000)
        self.assertEqual(_literal(COMPANION / "worker.py", "OPERATOR_DETAIL_MAX"), 2000)
        engine = (op / "engine.py").read_text(encoding="utf-8")
        self.assertIn('f"{ai} · {where}: {problem}"[:K.DETAIL_MAX]', engine)
        self.assertNotIn("self.rec[\"detail\"] = f\"{path}", engine)
        host = (op / "host.py").read_text(encoding="utf-8")
        self.assertIn('meta["evidence"] = doc.evidence', host)
        ack = method_source_of(COMPANION / "worker.py", "_ack_operator")
        self.assertIn("[:OPERATOR_DETAIL_MAX]", ack)



class ReceiptContract(unittest.TestCase):
    """收款单 RE（11-11，评估 docs/work/2026-11-11-collection-receipt-assessment.md「接口契约」）：CC 桌面版这一侧的约定。

    - 载荷 20（``direction = receipt``）CC 认得、是最高版本，worker 按方向交给 ``dbf_receipt``，代录标记不改道；
    - 写前核不过的四个码（契约原样小写）按「要人处理」回报，不白烧重试；
    - 新按需种类 ``express_ar_open``：读账套、结果属于连接与账套（ERP 后台发起）、2 MiB、租约 180 秒，有最低版本，两端同值；
      CC 登记了处理函数，参数只收两个位置键加可选 customer，不拿写者锁。"""

    def test_payload_20_is_the_receipt_and_the_worker_routes_it(self):
        self.assertIn(20, _literal(COMPANION / "payload_contract.py", "SUPPORTED_PAYLOAD_VERSIONS"))
        self.assertEqual(_literal(COMPANION / "payload_contract.py", "RECEIPT_PAYLOAD_VERSION"), 20)
        self.assertEqual(module_constants(COMPANION / "dbf_receipt.py")["DIRECTION"], "receipt")
        worker = (COMPANION / "worker.py").read_text(encoding="utf-8")
        self.assertIn('direction == "receipt"', worker)
        self.assertIn("write_receipt(payload, st.account_dir, **common)", worker)

    def test_the_refusal_codes_go_to_a_person(self):
        codes = module_constants(COMPANION / "dbf_receipt.py")
        names = ("ERR_ALREADY_COLLECTED", "ERR_INVOICE_NOT_FOUND", "ERR_RECEIPT_TYPE", "ERR_CASH_ACCOUNT")
        self.assertEqual(tuple(codes[name] for name in names),
                         ("already_collected", "invoice_not_found", "receipt_type_invalid", "cash_account_missing"))
        outcome = method_source_of(COMPANION / "worker.py", "_outcome_for")
        for name in names:
            self.assertIn(f"'{codes[name]}': 'needs_review'", outcome)

    def test_codes_the_server_acts_on_are_the_ones_cc_refuses_with_before_writing(self):
        """收款写前被拒退回草稿（主会话 11-14）：服务端认的码与 CC 回的码逐字相同（11-14 前服务端写成大写、从没对上过），
        而且每一个都在写前备份之前就定了：拒绝发生在 ``_backup`` 之前的检查里，写入阶段（``_write_all`` 里取单号）抛的拒绝
        走写入失败的码（``_error_code`` 只认 ``_WriteError``），不会被当成「写前就拒」。"""
        erp = module_constants(ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/collection/contracts.py")
        refusals = module_constants(COMPANION / "dbf_receipt.py")
        cc_codes = {refusals[name] for name in ("ERR_ALREADY_COLLECTED", "ERR_INVOICE_NOT_FOUND", "ERR_RECEIPT_TYPE",
                                                 "ERR_CASH_ACCOUNT", "ERR_BOOK", "ERR_CUSTOMER", "ERR_NOT_POSTABLE")}
        self.assertEqual(set(erp["RETURNABLE"]), cc_codes)
        self.assertLessEqual(set(erp["UNCOLLECTABLE"]), cc_codes)
        write = method_source_of(COMPANION / "dbf_receipt.py", "write_receipt")
        backup = write.index("_backup(")
        for check in ("_missing_tables(", "existing(", "_code_exists(", "run_row(", "plan_methods(", "plan_bank(",
                      "cash_account(", "receivable_account(", "check_invoices("):
            self.assertLess(write.index(check), backup, check)
        self.assertIn("except Refused as exc:", write[:backup])
        self.assertNotIn("Refused", write[backup:].split("except Exception")[0].replace("_next_docnum", ""))
        mapping = method_source_of(COMPANION / "dbf_writer.py", "_error_code")
        self.assertIn("isinstance(e, _WriteError)", mapping)
        self.assertNotIn("Refused", mapping)

    def test_the_request_key_never_reaches_cc_so_a_retried_key_works_on_1_12_21(self):
        """退回草稿再放行的请求号带次数（``erp:receipt:<记录号>:<次数>``）：请求号只在服务端写账队列去重，领单只交投递号、
        记录号、单号、载荷（与可选的 attended / task_key），CC 桌面版不读也不解析它；YOUREF 仍是记录号。"""
        job = method_source_of(WIRE / "queue_contracts.py", "job")
        self.assertNotIn("request_key", job)
        for key in ("'log_id'", "'history_id'", "'invoice_no'", "'payload'"):
            self.assertIn(key, job)
        for path in COMPANION.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("erp:receipt", text, path.name)
            if "request_key" in text:
                self.assertNotIn("task.request_key", text, path.name)
        push = (ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/collection/push.py").read_text(encoding="utf-8")
        self.assertIn('"ref_no": doc.name', push)

    def test_the_open_invoice_request_is_a_shared_account_read_on_both_sides(self):
        server = number_constants(WIRE / "request_contracts.py", ("KINDS", "ACCOUNT_KINDS", "SHARED_KINDS", "FOLDER_KINDS",
                                                                     "MAX_BYTES", "LEASE_SECONDS", "KIND_MIN_COMPANION",
                                                                     "LOCATION_KEYS"))
        for group in ("KINDS", "ACCOUNT_KINDS", "SHARED_KINDS"):
            self.assertIn("express_ar_open", server[group], group)
        self.assertNotIn("express_ar_open", server["FOLDER_KINDS"])
        self.assertEqual(server["MAX_BYTES"]["express_ar_open"], 2 * 1024 * 1024)
        self.assertEqual(server["LEASE_SECONDS"]["express_ar_open"], 180)
        self.assertIn("express_ar_open", server["KIND_MIN_COMPANION"])
        # 收款读写随 CC 1.12.20 一起发（主会话 11-11 定）：服务端只把未收清发票请求交给 1.12.20 起的电脑
        self.assertEqual(server["KIND_MIN_COMPANION"]["express_ar_open"], "1.12.20")
        helper = number_constants(COMPANION / "express_receivables.py", ("PARAM_KEYS", "OPTIONAL_KEYS"))
        self.assertEqual(set(helper["PARAM_KEYS"]), set(server["LOCATION_KEYS"]))
        # docnums（按单号查状态，1.12.22）只派给认得它的版本：`Release1122Edges`
        self.assertEqual(tuple(helper["OPTIONAL_KEYS"]), ("customer", "docnums"))
        self.assertIn('"express_ar_open": express_receivables.read_ar_open',
                      (COMPANION / "request_worker.py").read_text(encoding="utf-8"))
        tree = ast.parse((COMPANION / "express_receivables.py").read_text(encoding="utf-8"))
        imported = {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}
        self.assertFalse({"account_lock", "account_write_lock", "dbf_writer", "dbf_receipt"} & imported)

    def test_receipt_types_carry_the_account_class_both_sides_read_the_same(self):
        """11-12 按类放宽「只列收钱类」（docs/work/2026-11-12-collection-transfer-rule-stats.md）：CC 主档读回的每种收款类型另带
        ``account_class``（按账套自己的科目表结构判），服务端按它判转账；值两端同一组。只加不改：判不出结构 CC 不报这一栏，
        服务端没有这一栏照旧按名称判（老版 CC）。"""
        names = ("ACCOUNT_BANK", "ACCOUNT_CASH_GROUP", "ACCOUNT_CASH", "ACCOUNT_OTHER", "ACCOUNT_CLASSES")
        helper = number_constants(COMPANION / "payment_methods.py", names)
        server = number_constants(ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/collection/contracts.py", names)
        self.assertEqual(helper, server)
        self.assertEqual(tuple(server["ACCOUNT_CLASSES"]), ("bank", "cash_group", "cash", "other"))
        types = method_source_of(COMPANION / "payment_methods.py", "types")
        self.assertTrue(quoted(types, "account_class"))
        self.assertIn("if classes is not None", types)  # 判不出结构不报这一栏
        for key in ("code", "prefix", "name", "name_en", "bank", "account", "account_issue"):
            self.assertTrue(quoted(types, key), key)  # 原有各栏不动
        rule = method_source_of(ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/collection/contracts.py", "_transfer")
        self.assertTrue(quoted(rule, "account_class"))
        self.assertIn("if klass in ACCOUNT_CLASSES", rule)  # 没报或认不出的值退回名称规则

    def test_receipt_types_carry_their_bank_account_and_uses(self):
        """11-13 收款方式按钮「银行简称 ·账号末 4 位」（共享笔记「收款-LINE侧-交接-11-13.md」§4，CC 这一侧负责字段）：主档读回的每种方式
        可带 ``bank_account``（code / bank / short / number / branch / via，via 只有 shortnam 与 account 两种）与 ``uses``（原生收款单
        用过几次，整数）。只加不改：取不到户头不带这一栏，旧版 CC 两栏都没有，服务端照旧显示方式名。"""
        bank = method_source_of(COMPANION / "payment_methods.py", "bank_account_of")
        for key in ("code", "bank", "short", "number", "branch", "via"):
            self.assertTrue(quoted(bank, key), key)
        self.assertTrue(quoted(bank, "shortnam") and quoted(bank, "account"))
        masters = (COMPANION / "express_masters.py").read_text(encoding="utf-8")
        self.assertIn('row["bank_account"] = bank', masters)
        self.assertIn('row["uses"] = counts.get(row["prefix"], 0)', masters)
        self.assertIn("if bank:", masters)  # 取不到户头不带这一栏


class GuidedPayload(unittest.TestCase):
    """载荷 21「引导录入」（约定 docs/work/2026-11-12-cc-guided-entry-express-coverage.md 第 8 节，8.5 的六条）：版本、新键名单、
    盖版本的规则、新原因码名单两边一致；版本 ≤ 20 的载荷 CC 一个新键都不读；`cost_choice` 与 `due_date_be` 的取值口径。"""

    @classmethod
    def setUpClass(cls):
        import importlib

        sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
        sys.path.insert(0, str(ROOT / "companion" / "src"))
        try:
            cls.keys = importlib.import_module("companion.guided_keys")  # 纯函数，不依赖 dbf（CI 的 Python 没装 CC 的依赖）
            cls.client = importlib.import_module("companion.payload_contract")
        finally:
            sys.path.remove(str(ROOT / "companion" / "src"))
        from pearnly_core.cc_desktop.wire import ack_rules
        from pearnly_core.express_rules.push import common

        cls.common, cls.ack_rules = common, ack_rules

    @staticmethod
    def _sale(**header):
        return {"direction": "sales", "doctype": "IV", "docdate_be": "690320", "ref_no": "IV-1",
                "customer": {"code": "C001", "name": "ร้านป้าแดง"},
                "items": [{"name": "น้ำแข็ง", "qty": "2", "unit": "ถัง", "amount": "60.00", "item_mode": "stock_sale"}],
                "items_status": "ok", **header}

    def test_the_version_is_the_same_on_both_sides_and_cc_supports_it(self):
        self.assertEqual(self.common.GUIDED_PAYLOAD_VERSION, 21)
        self.assertEqual(self.common.GUIDED_PAYLOAD_VERSION, self.client.GUIDED_PAYLOAD_VERSION)
        self.assertIn(self.client.GUIDED_PAYLOAD_VERSION, self.client.SUPPORTED_PAYLOAD_VERSIONS)
        self.assertEqual(self.client.check_payload_version({"payload_version": 21}), None)

    def test_the_new_keys_are_the_same_on_both_sides(self):
        self.assertEqual(self.common.GUIDED_KEYS, self.client.GUIDED_KEYS)
        self.assertEqual(self.common.GUIDED_KEYS, {
            "header": ("guided", "due_date_be", "cash_account"),
            "line": ("unit_code", "acccod", "stkgrp", "cost_choice", "cost", "negallow"),
            "party": ("type", "payterm")})
        self.assertEqual(tuple(self.common.GUIDED_PARTY_BLOCKS), tuple(self.keys.PARTY_BLOCKS))
        self.assertEqual((self.keys.HEADER_KEYS, self.keys.LINE_KEYS, self.keys.PARTY_KEYS), tuple(self.client.GUIDED_KEYS.values()))
        # CC 适配层 / 写路读的就是这些键：每个键在 CC 源码里都出现过
        source = "".join((COMPANION / name).read_text(encoding="utf-8") for name in (
            "guided_keys.py", "guided.py", "purchase_adapter.py", "sales_adapter.py", "stock_purchase.py", "stock_sale.py",
            "stock_guard.py"))
        for group in self.client.GUIDED_KEYS.values():
            for key in group:
                self.assertTrue(quoted(source, key), key)

    def test_only_a_payload_with_a_new_key_is_stamped_21(self):
        plain = self.common.finalize_payload(self._sale())["payload_version"]
        self.assertLess(plain, 21)
        for key, value in (("guided", True), ("guided", False), ("due_date_be", "690419"), ("cash_account", "11-01-02-00")):
            self.assertEqual(self.common.finalize_payload(self._sale(**{key: value}))["payload_version"], 21, key)
        for key, value in (("unit_code", "ขด"), ("acccod", "ST02"), ("stkgrp", "FG"), ("cost_choice", "none"), ("cost", "4.5"),
                           ("negallow", "")):
            payload = self._sale()
            payload["items"][0][key] = value
            self.assertEqual(self.common.finalize_payload(payload)["payload_version"], 21, key)
        for block in ("customer", "supplier"):
            for key, value in (("type", "07"), ("payterm", 0)):
                payload = self._sale()
                payload[block] = {"code": "", "name": "ใหม่", key: value}
                self.assertEqual(self.common.finalize_payload(payload)["payload_version"], 21, (block, key))
        # 已有键不受影响：换算率仍盖 7、不到 21
        payload = self._sale()
        payload["items"][0]["factor"] = "12"
        self.assertEqual(self.common.finalize_payload(payload)["payload_version"], self.common.FACTOR_PAYLOAD_VERSION)
        for payload in (self._sale(), self._sale(guided=True)):
            self.assertEqual(self.common.guided_keys_used(payload), self.keys.uses(payload))

    def test_the_new_reason_codes_are_the_same_on_both_sides(self):
        self.assertEqual(tuple(self.common.GUIDED_CHOICE_CODES), tuple(self.client.GUIDED_CHOICE_CODES))
        self.assertEqual(tuple(self.common.GUIDED_REFUSAL_CODES), tuple(self.client.GUIDED_REFUSAL_CODES))
        self.assertEqual(set(self.common.GUIDED_CHOICE_CODES), {"UNIT_CHOICE", "COST_BASIS_CHOICE", "CASH_ACCOUNT_CHOICE"})
        self.assertEqual(set(self.common.GUIDED_REFUSAL_CODES),
                         {"UNIT_CODE_NOT_FOUND", "ITEM_GROUP_NOT_FOUND", "INVALID_DUE_DATE", "PARTY_TYPE_NOT_FOUND"})
        # 三个 *_CHOICE 在「回执转出题」名单里；七个码都直接转人工、不重试
        self.assertEqual(self.ack_rules.CHOICE_CODES, frozenset(self.common.GUIDED_CHOICE_CODES))
        for code in (*self.common.GUIDED_CHOICE_CODES, *self.common.GUIDED_REFUSAL_CODES):
            self.assertIn(code, self.ack_rules.NEEDS_PERSON_CODES, code)
            move = self.ack_rules.acknowledgement(branch="legacy", attempt=1, success=False, error=f"[{code}] x")
            self.assertEqual((move.status, move.retry), ("manual", False), code)
        # CC 端每个码都有定义，*_CHOICE 与账套里查不到的三个都报 needs_review
        worker = (COMPANION / "worker.py").read_text(encoding="utf-8")
        for code in (*self.common.GUIDED_CHOICE_CODES, *self.common.GUIDED_REFUSAL_CODES):
            self.assertEqual(getattr(self.keys, "ERR_" + code), code)
            if code != "INVALID_DUE_DATE":  # 到期日早于单据日是 PAYLOAD_INVALID 的子码
                self.assertIn(f'"{code}": "needs_review"', worker, code)

    def test_the_receipt_keys_for_a_choice_and_a_shortage_survive_the_server(self):
        """回执 meta.choice / meta.shortages（CC `worker._push_meta`）：服务端净化后还在，认不得的码与键丢掉。"""
        worker = (COMPANION / "worker.py").read_text(encoding="utf-8")
        self.assertIn('m["choice"]', worker)
        self.assertIn('m["shortages"]', worker)
        self.assertEqual((self.common.CHOICE_META, self.common.SHORTAGES_META), ("choice", "shortages"))
        line = {"line": 2, "name": "น้ำแข็งบด", "stkcod": "ICE02", "units": [{"code": "ถง", "factor": "1", "role": "stock"}]}
        choice = {"code": "UNIT_CHOICE", **line, "lines": [line], "unit_table": ["ถง", "กล"]}
        shortages = [{"stkcod": "ICE01", "loccod": "01", "need": "6", "have": "5"},
                     {"stkcod": "", "loccod": "01", "need": "2", "have": "0", "name": "น้ำแข็งบด", "new": True}]
        clean = self.common.sanitize_push_meta({"choice": {**choice, "junk": 1}, "shortages": shortages})
        self.assertEqual(clean, {"choice": choice, "shortages": shortages})
        cost = {"code": "COST_BASIS_CHOICE", "line": 3, "name": "ใหม่", "stkcod": "", "balance": "0"}
        self.assertEqual(self.common.sanitize_push_meta({"choice": cost})["choice"], cost)
        cash = {"code": "CASH_ACCOUNT_CHOICE", "cash_account": "11-01-00-00"}
        self.assertEqual(self.common.sanitize_push_meta({"choice": cash})["choice"], cash)
        self.assertEqual(self.common.sanitize_push_meta({"choice": {"code": "SOMETHING_ELSE"}, "shortages": [{"stkcod": "A"}]}), {})
        self.assertEqual(self.common.sanitize_push_meta({"docnum": "IV1"}), {"docnum": "IV1"})  # 不带时与以前相同
        # 库存不足是因为账套「扣成负库存要批准」（ISINFO.NEGAUTH）：meta.approval，只认 true
        self.assertIn('m["approval"] = True', worker)
        self.assertEqual(self.common.APPROVAL_META, "approval")
        self.assertEqual(self.common.sanitize_push_meta({"approval": True, "shortages": shortages}),
                         {"approval": True, "shortages": shortages})
        for junk in (False, "yes", 1, None):
            self.assertNotIn("approval", self.common.sanitize_push_meta({"approval": junk}))
        self.assertTrue(quoted((COMPANION / "stock_guard.py").read_text(encoding="utf-8"), "NEGAUTH"))

    def test_cells_of_the_account_balances_that_were_not_marked_reach_the_server(self):
        """科目余额标待重算（CC ``glbal_mark``，11-12）：没标上的格随回执 meta.glbal_unmarked 回来，服务端净化后还在。"""
        worker = (COMPANION / "worker.py").read_text(encoding="utf-8")
        self.assertIn('meta["glbal_unmarked"]', worker)
        self.assertEqual(self.common.GLBAL_UNMARKED_META, "glbal_unmarked")
        cells = ["21-02-01-00||12: no GLBAL row", "11-04-02-00||2027-01-05: period not in this fiscal year"]
        self.assertEqual(self.common.sanitize_push_meta({"glbal_unmarked": cells}), {"glbal_unmarked": cells})
        self.assertEqual(self.common.sanitize_push_meta({"glbal_unmarked": "x"}), {})
        # 每条写分录的直写都调同一步
        for name in ("dbf_writer.py", "dbf_sales.py", "dbf_refund.py", "pos_journal.py", "dbf_stock.py", "dbf_void.py", "dbf_receipt.py"):
            self.assertIn("glbal_mark.mark(account_dir,", (COMPANION / name).read_text(encoding="utf-8"), name)

    def test_a_purchase_without_an_invoice_number_is_not_refused_by_cc(self):
        """缺供应商发票号不再拒收（覆盖表 D-5）：CC 采购适配层不再回 MISSING_REF_NO；销售的票面号照旧必填。"""
        purchase = method_source_of(COMPANION / "purchase_adapter.py", "validate_purchase_payload")
        self.assertNotIn("ERR_MISSING_REF_NO", purchase)
        self.assertIn("ERR_MISSING_REF_NO", method_source_of(COMPANION / "sales_adapter.py", "validate_sales_payload"))

    def test_an_older_payload_reads_none_of_the_new_keys(self):
        """版本 ≤ 20 的载荷（不带 guided 的旧任务）：经 CC 新版适配层取出的明细、表头、对方块新键与没带的完全相同。
        （整张写入计划与写出的表逐字节相同由 companion/tests/test_guided_payload.py 在 Windows 检查里守。）"""
        stray = {"unit_code": "ขด", "acccod": "X", "stkgrp": "Y", "cost_choice": "given", "cost": "9", "negallow": "N"}
        for version in (None, 1, 7, 19, 20):
            plain = self._sale()
            dirty = self._sale(guided=True, due_date_be="690101", cash_account="X")
            dirty["items"][0].update(stray)
            dirty["customer"].update(type="ZZ", payterm=30)
            if version is not None:
                plain["payload_version"] = dirty["payload_version"] = version
            self.assertFalse(self.keys.active(dirty), version)
            self.assertFalse(self.keys.guided(dirty), version)
            self.assertEqual(self.keys.items(dirty), plain["items"], version)
            self.assertIs(self.keys.items(plain)[0], plain["items"][0])  # 没带新键的行是同一个对象
            for key in self.keys.HEADER_KEYS:
                self.assertIsNone(self.keys.header(dirty, key), key)
            for key in self.keys.PARTY_KEYS:
                self.assertIsNone(self.keys.party(dirty, "customer", key), key)
        modern = self._sale(guided=True, payload_version=21)
        self.assertTrue(self.keys.active(modern) and self.keys.guided(modern))
        self.assertFalse(self.keys.guided(self._sale(payload_version=21)))
        # 两个适配层都只在版本 ≥ 21 时看新键
        for name in ("purchase_adapter.py", "sales_adapter.py"):
            text = (COMPANION / name).read_text(encoding="utf-8")
            self.assertIn("if guided.active(payload):", text, name)
            self.assertIn("items=guided.items(payload)", text, name)

    def test_cost_choice_and_due_date_take_only_what_the_contract_allows(self):
        def problem(doctype="IV", block="customer", line=None, **header):
            payload = self._sale(doctype=doctype, payload_version=21, **header)
            payload["items"][0].update(line or {})
            return self.keys.shape_problem(payload, doctype, block)

        self.assertIsNone(problem(line={"cost_choice": "none"}))
        self.assertIsNone(problem(line={"cost_choice": "given", "cost": "4.50"}))
        for line in ({"cost_choice": "yes"}, {"cost_choice": "given"}, {"cost_choice": "given", "cost": "-1"},
                     {"cost_choice": "given", "cost": 4.5}, {"cost_choice": "none", "item_mode": "direct_account"}):
            self.assertEqual(problem(line=line)[0], "PAYLOAD_INVALID", line)
        self.assertIsNone(problem(due_date_be="690320"))
        self.assertIsNone(problem(doctype="RR", block="supplier", due_date_be="690419"))
        self.assertEqual(problem(due_date_be="690319")[0], "INVALID_DUE_DATE")
        for doctype in ("HS", "HP", "SR"):
            self.assertEqual(problem(doctype=doctype, due_date_be="690419")[0], "PAYLOAD_INVALID", doctype)
        self.assertIsNone(problem(doctype="HS", cash_account="11-01-02-00"))
        self.assertEqual(problem(doctype="IV", cash_account="11-01-02-00")[0], "PAYLOAD_INVALID")
        self.assertEqual(problem(doctype="HS", cash_account="11-01-02-00", pos={})[0], "PAYLOAD_INVALID")


class BookQuestions(unittest.TestCase):
    """开始前四题（A3 / P2-2，``express_rules.push.book_questions``）与 CC 桌面版写时的同一判法：常量逐个相同；代录单只带 CC 代录认的键
    （带了表头 ``guided`` / ``cash_account`` 或成本键，CC 整单拒收），所以代录不出成本、现金科目两题；直写下发的键都是载荷 21 的键。
    两边拿同一本合成账套真跑比结论在 ``companion/tests/test_seam_book_questions.py``。"""

    @classmethod
    def setUpClass(cls):
        import importlib

        sys.path.insert(0, str(ROOT / "apps" / "pearnly_core"))
        sys.path.insert(0, str(ROOT / "companion" / "src"))
        try:
            cls.keys = importlib.import_module("companion.guided_keys")  # 纯函数，不依赖 dbf
        finally:
            sys.path.remove(str(ROOT / "companion" / "src"))
        from pearnly_core.express_rules.push import book_questions

        cls.server = book_questions

    def test_the_constants_match_cc(self):
        for name in ("STOCK_IN", "STOCK_OUT", "CASH_DOCTYPES", "COST_GIVEN", "COST_NONE"):
            self.assertEqual(getattr(self.server, name), getattr(self.keys, name), name)
        self.assertEqual(self.server.UNIT_FALLBACK, module_constants(COMPANION / "dbf_schema.py")["_UNIT_MAP"])
        self.assertEqual(self.server.ALL_LOCATIONS, module_constants(COMPANION / "stock_guard.py")["ALL_LOCATIONS"])
        self.assertEqual(self.server.ALLOW, "Y")
        self.assertIn(self.server.ALLOW, self.keys.NEGALLOW_VALUES)

    @staticmethod
    def _payload(doctype="HS"):
        return {"direction": "sales", "doctype": doctype, "items_status": "ok", "payload_version": 21,
                "items": [{"name": "ใหม่", "code": "", "qty": "1", "unit": "x", "item_mode": "stock_sale"}]}

    def test_an_operator_payload_with_every_answer_still_passes_cc_operator_gate(self):
        B = self.server
        decided = {B.UNIT: {B.unit_subject("sales", {"name": "ใหม่", "unit": "x"}): "ชน"}, B.NEGATIVE: {"ใหม่": "Y"},
                   B.COST: {"line-1": "given:3"}, B.CASH: {"cash": "11-01-01-00"}}
        operator = B.apply(self._payload(), decided, mode=B.OPERATOR)
        self.assertTrue(self.keys.operator_allowed(operator))
        self.assertEqual(operator["items"][0]["unit_code"], "ชน")
        self.assertNotIn("guided", operator)
        self.assertNotIn("cash_account", operator)
        self.assertNotIn("cost_choice", operator["items"][0])
        direct = B.apply(self._payload(), decided)
        self.assertFalse(self.keys.operator_allowed(direct), "直写下发了成本、现金科目与 guided：这份不能当代录单交")
        self.assertEqual(self.keys.shape_problem(direct, "HS", "customer"), None)
        self.assertEqual(set(B.OPERATOR_KINDS), {B.UNIT, B.NEGATIVE})


if __name__ == "__main__":
    unittest.main()
