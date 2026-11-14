"""测试机接本机 Mac 实验栈的编排（scripts/acceptance/maclab_pc.py）与测试机侧的配置核对（infrastructure/maclab/win/cfg_guard.py）。

锁住：hosts 那一行的增补判定（已有同一行不动、同名字指向别处 / 只带一个名字就停）；依赖清单按 requirements-build.txt 钉的版本取、
少一个就拒绝；签码输出只认一行形状对的配对码、拒签时消息不带密钥；还原核对的三种结论（字节相同过、语义相同拷回、语义不同停下，
启用前备份不是期望的那份也停下）；JSON 差异只给键路径不给值；心跳日志判定（重启后要有 [heartbeat] INFO、其后没有心跳告警或错误、
续行并进上一条）；attach 的配对码只进 600 的临时文件、不进桌面用户通道的任何参数，事后核对测试机上的码文件已删。
"""
import importlib.util
import json
import stat
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pc = _load("maclab_pc", ROOT / "scripts" / "acceptance" / "maclab_pc.py")
guard = _load("maclab_cfg_guard", ROOT / "infrastructure" / "maclab" / "win" / "cfg_guard.py")
pair_profile = _load("maclab_pair_profile_pc", ROOT / "infrastructure" / "maclab" / "win" / "pair_profile.py")

HOSTS = "# Copyright (c) Microsoft\r\n#\t127.0.0.1       localhost\r\n10.0.0.5 accserver\r\n"
CODE = "pcc_PCD-0123456789abcdef0123_" + "Z" * 43 + "@erp.maclab.localhost:18991"


class Hosts(unittest.TestCase):
    def test_add_when_absent(self):
        self.assertEqual(pc.hosts_plan(HOSTS, "192.168.0.93"),
                         {"action": "add", "line": "192.168.0.93 erp.maclab.localhost cowork.maclab.localhost"})

    def test_same_line_is_left_alone(self):
        text = HOSTS + "192.168.0.93  erp.maclab.localhost cowork.maclab.localhost  # maclab\r\n"
        self.assertEqual(pc.hosts_plan(text, "192.168.0.93")["action"], "present")
        split = HOSTS + "192.168.0.93 erp.maclab.localhost\r\n192.168.0.93 COWORK.maclab.localhost\r\n"
        self.assertEqual(pc.hosts_plan(split, "192.168.0.93")["action"], "present")

    def test_other_address_or_half_line_stops(self):
        for text in (HOSTS + "192.168.0.50 erp.maclab.localhost cowork.maclab.localhost\r\n",
                     HOSTS + "192.168.0.93 erp.maclab.localhost\r\n",
                     HOSTS + "192.168.0.93 erp.maclab.localhost cowork.maclab.localhost\r\n10.1.1.1 cowork.maclab.localhost\r\n"):
            with self.subTest(text=text[-60:]):
                plan = pc.hosts_plan(text, "192.168.0.93")
                self.assertEqual(plan["action"], "conflict")
                self.assertTrue(plan["lines"])

    def test_commented_lines_do_not_count(self):
        text = HOSTS + "# 192.168.0.50 erp.maclab.localhost cowork.maclab.localhost\r\n"
        self.assertEqual(pc.hosts_plan(text, "192.168.0.93")["action"], "add")

    def test_ps1_refuses_existing_names_and_only_appends(self):
        script = (ROOT / "infrastructure" / "maclab" / "win" / "hosts_add.ps1").read_text(encoding="utf-8")
        self.assertIn("exit 3", script)
        self.assertIn("AppendAllText", script)
        self.assertIn("hosts-backup-", script)
        self.assertNotIn("WriteAllText", script)


class Dependencies(unittest.TestCase):
    def test_versions_come_from_the_build_requirements(self):
        versions = pc.pinned((ROOT / "companion" / "packaging" / "requirements-build.txt").read_text(encoding="utf-8"))
        self.assertEqual(list(versions), list(pc.DEPS))
        self.assertTrue(all(versions.values()))

    def test_missing_pin_is_refused(self):
        with self.assertRaises(ValueError):
            pc.pinned("httpx==0.28.1\n")
        self.assertEqual(pc.pinned("Typing-Extensions == 4.15.0  # x\n", ("typing_extensions",)), {"typing_extensions": "4.15.0"})

    def test_installed_list(self):
        self.assertEqual(pc.dist_versions(["httpx", "httpx-0.28.1.dist-info", "typing_extensions-4.15.0.dist-info", "bin"]),
                         {"httpx": "0.28.1", "typing_extensions": "4.15.0"})

    def test_profile_script_puts_its_own_dirs_first(self):
        ccsrc, site = Path("C:/acc/ccsrc"), Path("C:/acc/maclab/site")
        self.assertEqual(pair_profile.import_paths(ccsrc, site, True, True, True), [str(ccsrc), str(site)])
        self.assertEqual(pair_profile.import_paths(ccsrc, site, False, True, False), [str(ccsrc / "src")])


class Minting(unittest.TestCase):
    def test_one_well_formed_code(self):
        self.assertEqual(pc.pairing_code(f"DEVICE PCD-1\nPAIRING_CODE {CODE}\n"), CODE)

    def test_refusal_carries_no_secret(self):
        with self.assertRaises(pc.Unmet) as caught:
            pc.pairing_code('MINT_REFUSED {"status": "in_use", "generation": 1}\n')
        self.assertIn("in_use", str(caught.exception))
        for text in ("", "PAIRING_CODE pcc_x@testerp.pearnly.com\n", f"PAIRING_CODE {CODE}\nPAIRING_CODE {CODE}\n"):
            with self.subTest(text=text[:30]), self.assertRaises(pc.Unmet) as caught:
                pc.pairing_code(text)
            self.assertNotIn("Z" * 10, str(caught.exception))


class Release(unittest.TestCase):
    def test_decisions(self):
        same = {"hash": "C216460D", "before_hash": "C216460D", "bytes_equal": True, "semantic_equal": True}
        self.assertEqual(pc.release_decision(same), "ok")
        self.assertEqual(pc.release_decision(same, "c216460d"), "ok")
        self.assertEqual(pc.release_decision({**same, "before_hash": "AAAAAAAA"}, "C216460D"), "stop")
        moved = {"hash": "12345678", "before_hash": "C216460D", "bytes_equal": False, "semantic_equal": True}
        self.assertEqual(pc.release_decision(moved), "restore")
        self.assertEqual(pc.release_decision({**moved, "semantic_equal": False}), "stop")

    def test_names_from_show(self):
        self.assertEqual(pc.maclab_names({"maclab": {"maclab-erp": {}, "maclab-cowork": {}}}), ["maclab-cowork", "maclab-erp"])
        self.assertEqual(pc.maclab_names({}), [])


class Compare(unittest.TestCase):
    def test_paths_only(self):
        before = {"global": {"express_places": ["S:\\"], "place_channels": [["S:\\", ["cowork", "core"]]]},
                  "profiles": [{"profile_id": "a", "paused": False, "token_ciphertext": "dpapi:SECRET1"}]}
        after = json.loads(json.dumps(before))
        after["global"]["place_channels"][0][1] = ["cowork"]
        after["profiles"][0]["token_ciphertext"] = "dpapi:SECRET2"
        after["profiles"][0]["new"] = 1
        paths = guard.diff_paths(before, after)
        self.assertEqual(paths, ["global.place_channels[0][1][len 2→1]", "profiles[0].token_ciphertext", "profiles[0].new"])
        self.assertNotIn("SECRET", json.dumps(paths))

    def test_compare_bytes_and_meaning(self):
        doc = {"schema_version": 4, "global": {"a": 1}, "profiles": []}
        raw = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode()
        self.assertTrue(guard.compare(raw, raw)["ok"])
        other_layout = json.dumps(doc).encode()
        result = guard.compare(other_layout, raw)
        self.assertEqual((result["bytes_equal"], result["semantic_equal"], result["ok"]), (False, True, False))
        changed = guard.compare(json.dumps({**doc, "global": {"a": 2}}).encode(), raw)
        self.assertEqual((changed["semantic_equal"], changed["diff"]), (False, ["global.a"]))
        expect = guard.compare(raw, raw, guard.digest(raw).lower())
        self.assertTrue(expect["ok"] and expect["expect_matches_before"])
        self.assertFalse(guard.compare(raw, raw, "00000000")["ok"])
        self.assertEqual(guard.compare(b"{not json", raw)["semantic_equal"], False)


T0 = datetime(2026, 11, 14, 17, 0, 0)


def _log(*rows):
    return guard.parse_log([f"{(T0 + timedelta(seconds=s)):%Y-%m-%d %H:%M:%S},123 [{lvl}] pearnly_companion.worker :: {msg}"
                            if lvl else msg for s, lvl, msg in rows])


class Heartbeat(unittest.TestCase):
    def test_clean_beat_after_restart(self):
        entries = _log((-30, "WARNING", "[heartbeat] 上报失败(忽略): old"), (5, "INFO", "启动"),
                       (8, "INFO", "[heartbeat] 上报账套 1 个 · 可写 1 · 科目 120"))
        self.assertEqual(guard.heartbeat_verdict(entries, T0, T0 + timedelta(seconds=10))["state"], "waiting")
        verdict = guard.heartbeat_verdict(entries, T0, T0 + timedelta(seconds=30))
        self.assertEqual(verdict["state"], "ok")

    def test_warning_or_error_after_restart_fails(self):
        for rows in (((8, "INFO", "[heartbeat] 上报账套 1 个"), (12, "WARNING", "[heartbeat] 上报失败(忽略): 401")),
                     ((8, "INFO", "[heartbeat] 上报账套 1 个"), (9, "ERROR", "取任务出错"), (9, "", "Traceback (most recent call last):"))):
            with self.subTest(last=rows[-1][2]):
                verdict = guard.heartbeat_verdict(_log(*rows), T0, T0 + timedelta(seconds=60))
                self.assertEqual(verdict["state"], "failed")
        unrelated = _log((8, "INFO", "[heartbeat] 上报账套 1 个"), (9, "WARNING", "保持唤醒没设上"))
        self.assertEqual(guard.heartbeat_verdict(unrelated, T0, T0 + timedelta(seconds=60))["state"], "ok")

    def test_nothing_yet(self):
        self.assertEqual(guard.heartbeat_verdict(_log((3, "INFO", "启动")), T0, T0 + timedelta(seconds=200))["state"], "waiting")

    def test_continuation_lines_join(self):
        entries = _log((1, "ERROR", "boom"), (0, "", "  File x"), (2, "INFO", "next"))
        self.assertEqual(len(entries), 2)
        self.assertIn("File x", entries[0]["text"])


class Attach(unittest.TestCase):
    def test_code_only_in_a_private_file_and_gone_afterwards(self):
        calls, files = [], {}

        class FakeWin:
            def put(self, local, name=None):
                mode = stat.S_IMODE(Path(local).stat().st_mode)
                files[name] = (Path(local).read_text(encoding="utf-8"), mode)
                return "C:/PearnlyAcceptance/" + str(name)

        class FakePc:
            host = "testpc2"
            win = FakeWin()
            gone = False

            def mkdir(self, path):
                calls.append(("mkdir", path))

            def desk(self, script, *args, timeout=600):
                calls.append((script, args))
                if script == "pair_profile.py":
                    self.gone = True   # 脚本读完就删
                    return {"added": "maclab-erp", "site": "http://erp.maclab.localhost:18991", "channel": "core",
                            "account_sets": 1, "undo": {}, "config": {}}
                return {"created": True, "before_hash": "C216460D", "hash": "C216460D"}

            def exists(self, path):
                calls.append(("exists", path))
                return not self.gone

            def ssh(self, command, timeout=120):
                calls.append(("ssh", command))

        minted = mock.Mock(stdout=f"DEVICE PCD-1\nPAIRING_CODE {CODE}\n", stderr="", returncode=0)
        with mock.patch.object(pc.subprocess, "run", return_value=minted):
            out = pc.attach(FakePc(), "erp", "maclab-erp")
        self.assertTrue(out["code_file_gone"])
        self.assertFalse(out["code_file_left_by_script"])
        (name, (content, mode)), = files.items()
        self.assertEqual((content, mode), (CODE, 0o600))
        self.assertTrue(name.startswith("maclab/codes/") and name.endswith(".code"))
        secret = CODE.split("@")[0]
        self.assertNotIn(secret, json.dumps(calls))      # 不进桌面用户通道的参数、不进 ssh 命令
        self.assertNotIn(secret, json.dumps(out))
        self.assertEqual(calls[0], ("cfg_guard.py", ("snapshot", pc.BEFORE)))   # 启用前先记哈希与备份
        add = next(args for script, args in calls if script == "pair_profile.py")
        self.assertEqual(add[6:], ("add", "maclab-erp", pc.CODES + "\\" + name.rsplit("/", 1)[1], pc.ROOT_DIR, pc.LEDGER))


if __name__ == "__main__":
    sys.exit(not unittest.main(exit=False).result.wasSuccessful())
