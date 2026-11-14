"""本机 Mac 实验栈（infrastructure/maclab，scripts/maclab.sh）的离线检查。

锁住：
- 配对码解析与 CC 桌面版 ``pairing.split_pairing_code`` 逐例一致（测试机侧 pair_profile、本机心跳 probe 各有一份，不 import CC 的
  网络依赖；CC 的函数从源码里原样取出来跑）；通道前缀与 CC ``channel.PREFIXES`` 一致。
- 测试机侧新增档案的配置变换（pair_profile.activate / deactivate）：验收数据根只给这条通道、别的位置去掉这条通道、同通道的已有
  档案暂停、另一条通道不动；暂停 / 删除后原样还回去；验收数据根与已有位置互相包含时拒绝；输入不被改动。
- 叠加文件只把两个网页入口发布到 0.0.0.0，账本不发布、Keycloak 不起、不挂生产机上的主机路径；脚本语法正确。
"""
import ast
import copy
import importlib.util
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACLAB = ROOT / "infrastructure" / "maclab"
COMPANION = ROOT / "companion" / "src" / "companion"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pair_profile = _load("maclab_pair_profile", MACLAB / "win" / "pair_profile.py")
probe = _load("maclab_probe", MACLAB / "probe.py")


def _pick(path, names):
    """CC 源码里这几个顶层名字原样取出来（只要标准库）放进一个命名空间。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parts = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.Assign)):
            targets = [node.name] if isinstance(node, ast.FunctionDef) else [t.id for t in node.targets if isinstance(t, ast.Name)]
            if set(targets) & set(names):
                parts.append(ast.get_source_segment(path.read_text(encoding="utf-8"), node))
    namespace = {"re": re}
    exec("from __future__ import annotations\n" + "\n".join(parts), namespace)
    return namespace


CODES = [
    "pcc_PCD-0123456789abcdef0123_" + "a" * 43 + "@erp.maclab.localhost:18991",
    "exp_CWEC-1_" + "b" * 40 + "@cowork.maclab.localhost:18992",
    " exp_x_y@Cowork.Maclab.Localhost:18992 \n",
    "pcc_a_b@testerp.pearnly.com",
    "pcc_a_b@127.0.0.1:8000",
    "pcc_a_b@localhost",
    "pcc_a_b@intranet",            # 没有点、不是本机 → 不认
    "abc_a_b@erp.maclab.localhost:1",
    "pcc_a_b",
    "",
    "pcc_a_b@bad host",
    "pcc_a_b@x.y:123456",
]


class PairingCode(unittest.TestCase):
    def test_same_rule_as_cc(self):
        cc = _pick(COMPANION / "pairing.py", {"_HOST", "_LOCAL_HOSTS", "split_pairing_code"})["split_pairing_code"]
        for code in CODES:
            with self.subTest(code=code):
                self.assertEqual(pair_profile.split_code(code), cc(code))
                token, url = cc(code)
                self.assertEqual(probe.split_code(code), (token, url.split("://", 1)[1] if url else ""))

    def test_maclab_codes_use_plain_http(self):
        self.assertEqual(pair_profile.split_code(CODES[0])[1], "http://erp.maclab.localhost:18991")
        self.assertEqual(pair_profile.split_code(CODES[1])[1], "http://cowork.maclab.localhost:18992")

    def test_channel_prefixes_match_cc(self):
        cc = _pick(COMPANION / "channel.py", {"COWORK", "CORE", "PREFIXES"})["PREFIXES"]
        self.assertEqual(pair_profile.PREFIXES, cc)
        self.assertEqual(pair_profile.channel_of("pcc_x"), "core")
        self.assertEqual(pair_profile.channel_of("exp_x"), "cowork")
        self.assertEqual(probe.channel_paths()["path"]("pcc_x", "heartbeat"), "/api/method/pearnly_core.cc_desktop.machine.heartbeat")
        self.assertTrue(probe.channel_paths_payload_versions())


ACC = r"C:\PearnlyAcceptance\maclab-root"


def _config():
    return {
        "schema_version": 4,
        "global": {"express_places": ["S:\\", r"C:\PearnlyExpress", r"D:\Old"],
                   "place_channels": [["S:\\", ["cowork", "core"]], [r"C:\PearnlyExpress", ["core"]]]},
        "profiles": [
            {"profile_id": "cw", "channel": "cowork", "kind": "express", "paused": False, "authorized_roots": [r"S:\69EXP"]},
            {"profile_id": "erp", "channel": "core", "kind": "express", "paused": False,
             "authorized_roots": [r"C:\PearnlyExpress\70EXP", ACC + r"\sub"]},
            {"profile_id": "fold", "channel": "cowork", "kind": "folder", "paused": False},
            {"profile_id": "old", "channel": "core", "kind": "express", "paused": True, "authorized_roots": []},
            {"profile_id": "mac", "channel": "core", "kind": "express", "paused": False, "authorized_roots": [ACC]},
        ],
    }


class ProfileTransform(unittest.TestCase):
    def test_activate_isolates_the_new_profile(self):
        before = _config()
        snapshot = copy.deepcopy(before)
        out, undo = pair_profile.activate(before, "mac", "core", ACC)
        self.assertEqual(before, snapshot)  # 不改传入的那份
        box = out["global"]
        self.assertEqual(box["express_places"], ["S:\\", r"C:\PearnlyExpress", r"D:\Old", ACC])
        self.assertEqual(box["place_channels"], [["S:\\", ["cowork"]], [r"C:\PearnlyExpress", []], [r"D:\Old", ["cowork"]],
                                                 [ACC, ["core"]]])
        profiles = {item["profile_id"]: item for item in out["profiles"]}
        self.assertTrue(profiles["erp"]["paused"])          # 同通道的已有档案暂停
        self.assertFalse(profiles["cw"]["paused"])          # 另一条通道不动
        self.assertFalse(profiles["fold"]["paused"])
        self.assertTrue(profiles["old"]["paused"])          # 本来就暂停的不记
        self.assertFalse(profiles["mac"]["paused"])
        self.assertEqual(profiles["mac"]["authorized_roots"], [ACC])
        self.assertEqual(undo, {"added_root": True, "stripped": ["S:\\", r"C:\PearnlyExpress", r"D:\Old"], "paused": ["erp"]})

    def test_pause_gives_everything_back(self):
        before = _config()
        out, undo = pair_profile.activate(before, "mac", "core", ACC)
        back = pair_profile.deactivate(out, "mac", "core", ACC, undo)
        expected = _config()
        # 没登记的位置（D:\Old，CC 当两种都给）还回来时显式登记成两种都给；本档案暂停；同通道档案的授权根里验收数据根下的去掉
        expected["global"]["place_channels"] = [["S:\\", ["cowork", "core"]], [r"C:\PearnlyExpress", ["core"]],
                                                [r"D:\Old", ["cowork", "core"]]]
        expected["profiles"][4].update(paused=True, express_root=ACC)
        expected["profiles"][1]["authorized_roots"] = [r"C:\PearnlyExpress\70EXP"]
        self.assertEqual(back, expected)

    def test_remove_drops_the_profile(self):
        out, undo = pair_profile.activate(_config(), "mac", "core", ACC)
        back = pair_profile.deactivate(out, "mac", "core", ACC, undo, drop=True)
        self.assertEqual([item["profile_id"] for item in back["profiles"]], ["cw", "erp", "fold", "old"])
        self.assertFalse(back["profiles"][1]["paused"])

    def test_resume_after_pause_is_the_same_activation(self):
        first, undo = pair_profile.activate(_config(), "mac", "core", ACC)
        paused = pair_profile.deactivate(first, "mac", "core", ACC, undo)
        again, undo_again = pair_profile.activate(paused, "mac", "core", ACC)
        self.assertEqual(again["global"], first["global"] | {"place_channels": first["global"]["place_channels"]})
        self.assertEqual(undo_again["paused"], ["erp"])

    def test_cowork_profile_leaves_erp_alone(self):
        config = _config()
        config["profiles"][4]["channel"] = "cowork"
        out, undo = pair_profile.activate(config, "mac", "cowork", ACC)
        self.assertEqual(out["global"]["place_channels"], [["S:\\", ["core"]], [r"C:\PearnlyExpress", ["core"]], [r"D:\Old", ["core"]],
                                                           [ACC, ["cowork"]]])
        self.assertEqual(undo["paused"], ["cw"])
        self.assertFalse(next(p for p in out["profiles"] if p["profile_id"] == "erp")["paused"])

    def test_root_inside_an_existing_place_is_refused(self):
        for root in (r"S:\maclab", r"c:/pearnlyexpress/acc"):
            with self.subTest(root=root), self.assertRaises(ValueError):
                pair_profile.activate(_config(), "mac", "core", root)
        config = _config()
        config["global"]["express_places"].append(ACC + r"\inner")
        with self.assertRaises(ValueError):
            pair_profile.activate(config, "mac", "core", ACC)

    def test_unknown_profile_or_channel(self):
        with self.assertRaises(ValueError):
            pair_profile.activate(_config(), "nope", "core", ACC)
        with self.assertRaises(ValueError):
            pair_profile.activate(_config(), "mac", "dms", ACC)

    def test_view_has_no_secrets(self):
        config = _config()
        config["profiles"][4]["token_ciphertext"] = "dpapi-current-user:v1:SECRET"
        text = json.dumps(pair_profile.view(config, {"mac": "maclab-erp"}))
        self.assertNotIn("SECRET", text)
        self.assertIn("maclab-erp", text)


class StackFiles(unittest.TestCase):
    def setUp(self):
        self.overlay = (MACLAB / "override.yml").read_text(encoding="utf-8")

    def test_only_web_entries_are_public(self):
        public = re.findall(r"0\.0\.0\.0:\$\{MACLAB_PORT:\?\}:8080", self.overlay)
        self.assertEqual(len(public), 2)
        self.assertRegex(self.overlay, r"billing-web:\n(?:    .*\n)*?    ports: !reset \[\]")
        self.assertRegex(self.overlay, r"keycloak:\n    profiles: \[keycloak\]")

    def test_no_production_host_paths(self):
        body = "\n".join(line for line in self.overlay.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("/opt/pearnly", body)
        self.assertNotIn("/var/www", body)
        production = (ROOT / "infrastructure" / "production" / "compose.yml").read_text(encoding="utf-8")
        # 生产里挂了主机路径的应用服务，叠加文件都用 !override 整个换掉了卷
        for service in ("erp-web", "erp-worker", "erp-masters-worker", "erp-ocr-worker", "cowork-web", "cowork-worker",
                        "cowork-long-worker", "cowork-ocr-worker", "billing-web"):
            with self.subTest(service=service):
                self.assertIn(f"  {service}:\n", production)
                self.assertRegex(self.overlay, rf"  {service}:\n(?:    .*\n)*?    volumes: !override\n")

    def test_scripts_parse(self):
        subprocess.run(["bash", "-n", str(ROOT / "scripts" / "maclab.sh")], check=True)
        for path in MACLAB.rglob("*.py"):
            with self.subTest(path=path.name):
                ast.parse(path.read_text(encoding="utf-8"))

    def test_container_scripts_refuse_foreign_sites(self):
        for name in ("init.py", "mint.py", "finish.py"):
            with self.subTest(name=name):
                self.assertIn(".maclab.localhost", (MACLAB / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
