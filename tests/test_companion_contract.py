"""Windows 小助手（`companion/`）与服务端之间的约定：两边在同一个仓库里，改一边另一边必须跟上。

只读源码（AST / 文本），不导入 Frappe、不起服务，CI 离线跑。守住的是 2026-09-27 那次漏掉的几处：
- 小助手调用的三个机器接口 = 服务端 `http_auth` 认的三个原生地址，且都是只收 POST 的白名单方法；
- 网页复制出的配对码是 `密钥@本站域名`，小助手的 `split_pairing_code` 能拆开；
- 下载地址三处一致：小助手查更新、nginx 下载目录、发布接收脚本，以及网页「下载助手」的默认地址。
"""
import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "companion" / "src" / "companion"
SERVER = ROOT / "apps" / "pearnly_cowork" / "pearnly_cowork" / "companion"


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
    def test_the_web_copies_key_at_this_site_and_the_helper_splits_it(self):
        web = (ROOT / "apps/pearnly_cowork/pearnly_cowork/public/desk/targets.js").read_text(encoding="utf-8")
        self.assertIn("copy_to_clipboard(`${token}@${window.location.host}`)", web)
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


if __name__ == "__main__":
    unittest.main()
