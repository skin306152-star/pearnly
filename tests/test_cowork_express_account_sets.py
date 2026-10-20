"""CC 桌面版多台电脑第 2 批（ADR-021，方案 2.3、2.4、2.7、4.1 第 2–3 步）：账套登记、可达、账套资料。

- 身份认定纯函数 `targets.identity.resolve`（目录文件号 → 网络路径 + 主机别名 → 本机写法 → 旧版路径 → 待认定 → 新登记），
  按条表驱动；不变量：规范键与规范根登记后永不改；
- 池（`connections.pool`）与心跳（`collector.ingest`）：心跳不再写连接配置、不升版本，目录是各台可达行的并集，
  报空名单只清自己，主档按账套存、每条连接 20 份、被未了结推送记录引用的不淘汰；
- 目录刷新改派（`connections.refresh`）：账套级按「发起人自己的电脑 → 常驻 → 其他」交给够得到它的一台，整目录按在线
  电脑各一份子租约；别的电脑的心跳不判失败；
- 补丁 `v0_0_20_express_account_sets`：规范键 = 今天的 ``source_id``、旧电脑的可达行、引用到的键、账套资料，幂等，
  不动连接配置与版本号；
- 单槽读者改读新位置：推送准备的证据与主档、存货主档数回写、存货科目组、要人选的项。

不连库、不依赖 Frappe（`cowork_pool_fake`）；真实 Site 上的迁移与并发在第 7 批联调。
"""
import json
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import cowork_pool_fake as fake  # noqa: E402

frappe = fake.install()
DB, Doc = fake.DB, fake.Doc

from pearnly_cowork.connections import (  # noqa: E402
    account_masters, model, pool, preparation, refresh, state, stock_effects)
from pearnly_cowork.connections.reports import _ACCOUNT_SET_KEYS, _account_sets  # noqa: E402
from pearnly_cowork.targets import identity  # noqa: E402
from pearnly_cowork.targets.identity import Registered as R, resolve  # noqa: E402
from pearnly_cowork.targets.selection import require_catalog_evidence, resolve_account_choice  # noqa: E402
from pearnly_cowork.targets.contracts import TargetSelectionError  # noqa: E402

APP = fake.APP / "pearnly_cowork"
NOW = datetime(2026, 10, 16, 3, 0, 0)
TAX = {"tax_id": "0105559000001", "branch": "00000", "period_start": "2026-01-01"}
ALIASES = identity.host_aliases({"CCD-A": ["ACC-01", "acc-01.office.lan", "192.168.1.5", "172.27.224.1"],
                                 "CCD-B": ["ACC-02", "192.168.1.6", "172.27.224.1"]})


def row(path, root=None, *, uid=None, share=None, label=None, **attributes):
    values = {"path": path, "root": root if root is not None else path.rsplit("\\", 1)[0], **attributes}
    if uid is not None:
        values["uid"] = uid
    if share is not None:
        values["share"] = share
    return {"source_id": identity.normalize_express_account_key(path), "label": label or path.rsplit("\\", 1)[-1],
            "attributes": values}


def decide(item, registered=(), own=None, legacy=False, host="acc-02"):
    return resolve(item, registered=list(registered), own_reach=own or {}, legacy=legacy, host=host, aliases=ALIASES)


# ── 身份认定纯函数（方案 2.4）────────────────────────────────────────────────────────


class Identity(unittest.TestCase):
    SHARED = r"\\acc-01\exp\2569\book"

    def test_host_aliases_name_ip_and_dns_and_drop_ambiguous(self):
        self.assertEqual(ALIASES["192.168.1.5"], "acc-01")
        self.assertEqual(ALIASES["acc-01.office.lan"], "acc-01")
        self.assertEqual(ALIASES["acc-01"], "acc-01")
        # WSL / Hyper-V 网段在两台上都报了同一个地址：不猜
        self.assertNotIn("172.27.224.1", ALIASES)

    def test_location_normalizes_ip_name_case_and_loopback(self):
        for share in (r"\\192.168.1.5\EXP\2569\Book", r"\\ACC-01\exp\2569\BOOK", r"\\acc-01.office.lan\exp\2569\book",
                      "//acc-01/exp/2569/book/"):
            self.assertEqual(identity.normalize_location(share, aliases=ALIASES), self.SHARED, share)
        self.assertEqual(identity.normalize_location(r"\\localhost\exp\2569\book", aliases=ALIASES, own_host="acc-01"),
                         self.SHARED)
        self.assertEqual(identity.normalize_location(r"S:\2569\book", aliases=ALIASES), "")
        self.assertEqual(identity.normalize_location(None), "")

    def test_content_key(self):
        self.assertEqual(identity.content_key(TAX), "tax:0105559000001:00000:2026-01-01")
        self.assertEqual(identity.content_key({"company": " ABC Co ", "code": "X1", "period_start": "2026-01-01"}),
                         "name:abc co:x1:2026-01-01")
        self.assertEqual(identity.content_key({"code": "X1"}), "")

    def test_host_qualified_and_network_root(self):
        self.assertEqual(identity.host_qualified(r"C:\EXP\Book", "acc-02"), r"\\acc-02\c$\exp\book")
        self.assertEqual(identity.host_qualified(r"\\srv\x\y", "acc-02"), r"\\srv\x\y")
        # 映射盘：程序目录换成同一个共享下的网络写法
        self.assertEqual(identity.network_root(r"Z:\2569\EXP69\BOOK", r"\\srv\pas\2569\exp69\book", r"Z:\2569\EXP69",
                                               "acc-02"), r"\\srv\pas\2569\exp69")
        # 本机共享：前缀不是盘符
        self.assertEqual(identity.network_root(r"C:\PearnlyExpress\T7\BOOK", r"\\acc-01\pearnlyexpressro\t7\book",
                                               r"C:\PearnlyExpress", "acc-01"), r"\\acc-01\pearnlyexpressro")
        # 程序目录在别处（另一台服务器）：网络写法原样，本机盘符写成带主机名的
        self.assertEqual(identity.network_root(r"Z:\d\b", r"\\srv\s\d\b", r"\\AccServer\ACCOUNT\69EXP", "acc-02"),
                         r"\\accserver\account\69exp")
        self.assertEqual(identity.network_root(r"Z:\d\b", r"\\srv\s\d\b", r"D:\Program\EXP", "acc-02"),
                         r"\\acc-02\d$\program\exp")

    def test_rule_1_the_folder_id_wins_over_any_spelling(self):
        found = decide(row(r"Z:\2569\BOOK", uid="C8D5876C:0001"), [R("s:\\2569\\book", "s:\\2569", uid="c8d5876c:0001")])
        self.assertEqual((found.kind, found.rule, found.account_key, found.root_key),
                         ("match", 1, "s:\\2569\\book", "s:\\2569"))

    def test_rule_2_same_network_place_with_the_same_content(self):
        registered = [R("s:\\2569\\book", "s:\\2569", location=self.SHARED, content_key=identity.content_key(TAX))]
        found = decide(row(r"Y:\2569\BOOK", share=r"\\192.168.1.5\EXP\2569\BOOK", uid="u-new", **TAX), registered)
        self.assertEqual((found.kind, found.rule, found.account_key, found.uid), ("match", 2, "s:\\2569\\book", "u-new"))
        # 同一处、内容键不同：不按第 2 条认
        other = decide(row(r"Y:\2569\BOOK", share=r"\\192.168.1.5\EXP\2569\BOOK", tax_id="999"), registered)
        self.assertNotEqual(other.rule, 2)

    def test_rule_3_the_same_local_path_on_the_same_computer(self):
        registered = [R("s:\\2569\\book", "s:\\2569")]
        found = decide(row(r"S:\2569\BOOK", uid="u1", share=self.SHARED), registered, own={"s:\\2569\\book": "s:\\2569\\book"})
        self.assertEqual((found.kind, found.rule, found.uid, found.location), ("match", 3, "u1", self.SHARED))
        # 恢复到原处（文件号变了、只有这台够得到）：仍是它
        restored = [R("c:\\exp\\book", "c:\\exp", uid="u-old")]
        self.assertEqual(decide(row(r"C:\EXP\BOOK", uid="u-new"), restored, own={"c:\\exp\\book": "c:\\exp\\book"}).rule, 3)

    def test_rule_3_a_row_without_identity_stays_the_same_local_account_set(self):
        """10-20 测试机：本机账套目录改名后（1.9.0 仍报那一行，文件号、网络路径都读不出，显示属性沿用上次的），原来按文件号
        认上、带着旧共享写法的那份登记不另登记成带本机名的管理共享写法；内容键对不上（同一路径换了别的账套）照旧新登记。"""
        content = identity.content_key(TAX)
        known = R("c:\\pearnlyexpress\\70exp\\t8sincer", "c:\\pearnlyexpress\\70exp", uid="aa:1",
                  location=r"\\acc-02\pearnlyexpressro\70exp\t8sincer", content_key=content)
        own = {"c:\\pearnlyexpress\\70exp\\t8sincer": known.account_key}
        blind = decide(row(r"C:\PearnlyExpress\70EXP\T8SINCER", uid="", share="", **TAX), [known], own=own)
        self.assertEqual((blind.kind, blind.rule, blind.account_key, blind.uid), ("match", 3, known.account_key, ""))
        bare = decide(row(r"C:\PearnlyExpress\70EXP\T8SINCER", uid="", share=""), [known], own=own)
        self.assertEqual((bare.kind, bare.account_key), ("match", known.account_key))
        other = decide(row(r"C:\PearnlyExpress\70EXP\T8SINCER", uid="", share="", tax_id="9"), [known], own=own)
        self.assertEqual((other.kind, other.account_key), ("new", r"\\acc-02\c$\pearnlyexpress\70exp\t8sincer"))
        # 没用这个路径够到过它的电脑：照旧新登记、键带主机名（第 4 条用例同口径）
        self.assertEqual(decide(row(r"C:\PearnlyExpress\70EXP\T8SINCER", **TAX), [known]).kind, "new")

    def test_rule_3_never_merges_two_local_copies_at_the_same_path(self):
        copies = [R("c:\\exp\\book", "c:\\exp", uid="u-a", reached_by_others=True)]
        found = decide(row(r"C:\EXP\BOOK", uid="u-b"), copies, own={"c:\\exp\\book": "c:\\exp\\book"})
        self.assertEqual((found.kind, found.rule, found.account_key, found.root_key, found.local),
                         ("new", 6, r"\\acc-02\c$\exp\book", r"\\acc-02\c$\exp", True))

    def test_rule_4_an_old_desktop_keeps_todays_path_identity(self):
        self.assertEqual(decide(row(r"S:\2569\EXP69\BOOK"), [R("s:\\2569\\exp69\\book")], legacy=True).rule, 4)
        found = decide(row(r"S:\2569\EXP69\BOOK", r"S:\2569\EXP69"), [], legacy=True)
        self.assertEqual((found.kind, found.account_key, found.root_key, found.local),
                         ("new", "s:\\2569\\exp69\\book", "s:\\2569\\exp69", True))
        # 新版报不出文件号、也不在共享上的本机账套：不按旧版口径，键带主机名（两台同一路径不会撞）
        new = decide(row(r"S:\2569\EXP69\BOOK"), [R("s:\\2569\\exp69\\book")])
        self.assertEqual((new.rule, new.account_key), (6, r"\\acc-02\s$\2569\exp69\book"))

    def test_rule_5_pending_until_the_old_computer_reports_its_folder_id(self):
        old = R("s:\\2569\\exp69\\book", "s:\\2569\\exp69", content_key=identity.content_key(TAX), reached_by_others=True,
                others_live=True)
        item = row(r"Z:\2569\EXP69\BOOK", r"Z:\2569\EXP69", uid="u9", share=r"\\srv\pas\2569\EXP69\BOOK", **TAX)
        found = decide(item, [old])
        self.assertEqual((found.kind, found.rule, found.account_key), ("pending", 5, "s:\\2569\\exp69\\book"))
        # 退出条件：上报它的电脑都 7 天没报到或被移除 → 第 6 条新建（与旧登记内容键相同，company 锁让两者串行）
        gone = R(old.account_key, old.root_key, content_key=old.content_key, reached_by_others=True, others_live=False)
        found = decide(item, [gone])
        self.assertEqual((found.kind, found.rule, found.account_key, found.root_key),
                         ("new", 6, r"\\srv\pas\2569\exp69\book", r"\\srv\pas\2569\exp69"))
        self.assertFalse(found.local)
        # 内容键为空、或那份已经报过文件号：不等
        self.assertEqual(decide(row(r"Z:\x\b", uid="u9", share=r"\\srv\pas\x\b"), [old]).kind, "new")
        known = R(old.account_key, old.root_key, uid="u-other", content_key=old.content_key, others_live=True)
        self.assertEqual(decide(item, [known]).kind, "new")

    def test_rule_6_the_same_path_string_is_the_same_place(self):
        found = decide(row(r"Z:\2569\BOOK", share=r"\\192.168.1.5\exp\2569\book", tax_id="1"),
                       [R(self.SHARED, r"\\acc-01\exp\2569", location=self.SHARED, content_key="tax:2::")])
        self.assertEqual((found.kind, found.rule, found.account_key), ("match", 6, self.SHARED))

    def test_rule_1_without_a_network_path_never_takes_another_computers_own_copy(self):
        """审查 3：整盘克隆的两台电脑，各自本机拷贝的文件号相同；只属于另一台、它还够得到的登记不认。"""
        theirs = R(r"\\acc-01\c$\exp\book", r"\\acc-01\c$\exp", uid="0c9a:1", scope_device="CCD-A", foreign=True)
        found = decide(row(r"C:\EXP\BOOK", r"C:\EXP", uid="0c9a:1"), [theirs])
        self.assertEqual((found.kind, found.rule, found.account_key), ("new", 6, r"\\acc-02\c$\exp\book"))
        # 经网络路径够到它（A 把本机目录共享出来）：同一个文件号就是它
        shared = decide(row(r"Y:\BOOK", r"Y:", uid="0c9a:1", share=r"\\acc-01\exp\book"), [theirs])
        self.assertEqual((shared.kind, shared.rule), ("match", 1))
        # 那台已经够不到它（被移除、换了配对码）：照认
        gone = theirs._replace(foreign=False)
        self.assertEqual(decide(row(r"C:\EXP\BOOK", r"C:\EXP", uid="0c9a:1"), [gone]).rule, 1)

    def test_rule_3_needs_the_same_place(self):
        """审查 2：盘符改映射到另一处（网络路径、内容键都对不上）不按本机路径认，落到第 6 条新登记。"""
        x, y = {"tax_id": "1", "period_start": "2026-01-01"}, {"tax_id": "2", "period_start": "2026-01-01"}
        nas1 = R(r"\\nas1\exp\book", r"\\nas1\exp", uid="aa:1", location=r"\\nas1\exp\book",
                 content_key=identity.content_key(x))
        own = {"s:\\exp\\book": nas1.account_key}
        moved = decide(row(r"S:\EXP\BOOK", r"S:\EXP", uid="bb:2", share=r"\\nas2\exp\book", **y), [nas1], own=own)
        self.assertEqual((moved.kind, moved.rule, moved.account_key), ("new", 6, r"\\nas2\exp\book"))
        # 原处恢复（网络路径、内容键都相同，文件号变了）：仍是它
        restored = decide(row(r"S:\EXP\BOOK", r"S:\EXP", uid="cc:3", share=r"\\NAS1\EXP\BOOK", **x), [nas1], own=own)
        self.assertEqual((restored.kind, restored.account_key), ("match", nas1.account_key))
        # 升级：那份登记还没报过文件号 / 网络路径
        legacy = R("s:\\exp\\book", "s:\\exp", content_key=identity.content_key(x))
        upgraded = decide(row(r"S:\EXP\BOOK", r"S:\EXP", uid="bb:2", share=r"\\nas2\exp\book", **y), [legacy],
                          own={"s:\\exp\\book": "s:\\exp\\book"})
        self.assertEqual((upgraded.kind, upgraded.rule, upgraded.account_key), ("match", 3, "s:\\exp\\book"))

    def test_rule_4_an_old_desktop_never_takes_another_live_computers_own_copy(self):
        theirs = R("s:\\x\\book", "s:\\x", scope_device="CCD-L1", foreign=True)
        found = decide(row(r"S:\X\BOOK", r"S:\X"), [theirs], legacy=True)
        self.assertEqual((found.kind, found.rule, found.account_key, found.root_key, found.local),
                         ("new", 4, r"\\acc-02\s$\x\book", r"\\acc-02\s$\x", True))
        # 网络路径写不成带主机名的：对谁都是同一处
        unc = R(r"\\srv\x\book", r"\\srv\x", scope_device="CCD-L1", foreign=True)
        self.assertEqual(decide(row(r"\\SRV\X\BOOK", r"\\SRV\X"), [unc], legacy=True).kind, "match")
        self.assertEqual(decide(row(r"S:\X\BOOK", r"S:\X"), [theirs._replace(foreign=False)], legacy=True).rule, 4)

    def test_rule_6_a_conflicting_folder_id_at_the_same_path_waits(self):
        """审查 4：不同办公室 / VPN 下同一个 IP 的两台 NAS：同一个路径串，文件号不同、原文件号还有电脑在报 → 待认定。"""
        place = r"\\192.168.1.10\exp\book"
        first = R(place, r"\\192.168.1.10\exp", uid="aa:1", location=place, content_key="tax:1::",
                  others_uids=frozenset({"aa:1"}))
        item = row(r"S:\EXP\BOOK", r"S:\EXP", uid="bb:2", share=place, tax_id="2")
        found = decide(item, [first])
        self.assertEqual((found.kind, found.rule, found.account_key), ("pending", 6, place))
        # 原文件号没人再报了（那台 7 天没报到、被移除）：就是它
        self.assertEqual(decide(item, [first._replace(others_uids=frozenset())]).kind, "match")

    def test_invariant_a_match_never_proposes_another_key_or_root(self):
        registered = [R("s:\\2569\\book", "s:\\2569", uid="u1"), R(self.SHARED, "", location=self.SHARED)]
        for item in (row(r"Q:\other\root\BOOK", r"Q:\other\root", uid="u1"),
                     row(r"Y:\x", r"Y:\elsewhere", share=self.SHARED)):
            found = decide(item, registered)
            self.assertEqual(found.kind, "match")
            current = next(r for r in registered if r.account_key == found.account_key)
            self.assertEqual(found.root_key, current.root_key)

    def test_identity_module_stays_framework_free(self):
        source = (APP / "targets" / "identity.py").read_text()
        self.assertNotIn("import frappe", source)
        self.assertNotIn("from frappe", source)


class Whitelists(unittest.TestCase):
    """新版 CC 桌面版（第 5 批）在账套行上加报 ``uid`` / ``share``：两处白名单都收，旧版不报照旧。"""

    def test_both_sanitizers_keep_uid_and_share(self):
        self.assertTrue({"uid", "share"} <= set(_ACCOUNT_SET_KEYS))
        rows = _account_sets([{"code": "B", "path": r"S:\X\B", "root": r"S:\X", "uid": "v:1", "share": r"\\srv\s\X\B"}])
        self.assertEqual((rows[0]["attributes"]["uid"], rows[0]["attributes"]["share"]), ("v:1", r"\\srv\s\X\B"))


# ── 池与心跳 ─────────────────────────────────────────────────────────────────────────


def connection(name="CONN", **extra):
    doc = Doc(doctype="Cowork ERP Connection", name=name, workspace="W", client="", adapter="express", enabled=1,
              binding_generation=0, record_revision=1, projection_heads="{}", report_json="{}",
              config_json=model.dumps({"account_set": r"S:\OLD\DEFAULT", "express_root": r"S:\OLD",
                                       "revenue_acc": "4100", "directions": "purchase,sales"}), **extra)
    DB.docs.setdefault("Cowork ERP Connection", {})[name] = doc
    return doc


def body(*rows, selected=None, unchanged=False, **extra):
    result = {"companion_version": "1.6.0", **extra}
    if unchanged:
        result["account_sets_unchanged"] = True
    else:
        result["account_sets"] = [{"code": r["source_id"].rsplit("\\", 1)[-1], **r["attributes"]} for r in rows]
    if selected:
        result["account_set"] = selected
    return result


def union(name="CONN"):
    _, _, key = state.scope(None)
    head = state.heads(DB.docs["Cowork ERP Connection"][name]).get(key) or {}
    snapshot = DB.docs.get("Cowork ERP Snapshot", {}).get(head.get("snapshot_id"))
    return {item["source_id"]: item for item in model.parse(snapshot.body)["account_sets"]} if snapshot else {}


def reach(device=None):
    return {(r.device, r.account_key): r for r in DB.rows(pool.REACH) if device is None or r.device == device}


class Base(unittest.TestCase):
    def setUp(self):
        DB.clear()
        from pearnly_cowork.pearnly_cowork.doctype.cowork_express_account_set.cowork_express_account_set import (
            CoworkExpressAccountSet)
        DB.checks[pool.ACCOUNT_SET] = lambda doc, before: CoworkExpressAccountSet(doc).validate()
        self.doc = connection()
        self.a = fake.Computer(self.doc, "CCD-A", credential="KA", legacy=False, host="acc-01", aliases=ALIASES)
        self.b = fake.Computer(self.doc, "CCD-B", credential="KB", legacy=False, host="acc-02", aliases=ALIASES)


class Pool(Base):
    S_BOOK = row(r"S:\2569\EXP69\BOOK", r"S:\2569\EXP69", uid="v1:0001", share=r"\\srv\pas\2569\EXP69\BOOK", **TAX)
    Z_BOOK = row(r"Z:\2569\EXP69\BOOK", r"Z:\2569\EXP69", uid="V1:0001", share=r"\\SRV\pas\2569\EXP69\BOOK", **TAX)
    KEY = r"\\srv\pas\2569\exp69\book"

    def test_two_computers_mapping_one_share_as_different_drives_are_one_account_set(self):
        """场景②：A 映射 S:、B 映射 Z:，同一目录文件号相同 → 同一份登记，目录里一行，各自用自己的写法。"""
        self.a.report(body(self.S_BOOK, selected=r"S:\2569\EXP69\BOOK"), NOW)
        self.b.report(body(self.Z_BOOK, selected=r"Z:\2569\EXP69\BOOK"), NOW)
        self.assertEqual(list(union()), [self.KEY])
        self.assertEqual(union()[self.KEY]["attributes"]["root"], r"\\srv\pas\2569\exp69")
        self.assertNotIn("uid", union()[self.KEY]["attributes"])  # 每台电脑自己的写法不进池目录
        self.assertEqual(pool.local_paths("CONN", "CCD-B"), {self.KEY: r"Z:\2569\EXP69\BOOK"})
        self.assertEqual(pool.canonical("CONN", "CCD-A", r"s:\2569\exp69\book"), self.KEY)
        self.assertEqual(len(DB.rows(pool.ACCOUNT_SET)), 1)

    def test_local_copies_at_the_same_path_on_two_computers_never_collide(self):
        """场景③：各在本机、默认路径完全相同 → 文件号不同，各是各的登记，键带主机名。"""
        self.a.report(body(row(r"C:\EXP\BOOK", r"C:\EXP", uid="a:1", **TAX)), NOW)
        self.b.report(body(row(r"C:\EXP\BOOK", r"C:\EXP", uid="b:1", **TAX)), NOW)
        self.assertEqual(sorted(union()), [r"\\acc-01\c$\exp\book", r"\\acc-02\c$\exp\book"])
        registrations = {r.account_key: r for r in DB.rows(pool.ACCOUNT_SET)}
        self.assertEqual(registrations[r"\\acc-02\c$\exp\book"].scope_device, "CCD-B")
        self.assertEqual(registrations[r"\\acc-02\c$\exp\book"].root_key, r"\\acc-02\c$\exp")
        self.assertTrue(pool.reaches("CONN", "CCD-A", r"\\acc-01\c$\exp\book"))
        self.assertFalse(pool.reaches("CONN", "CCD-A", r"\\acc-02\c$\exp\book"))

    def test_heartbeats_never_touch_the_connection_configuration(self):
        """方案 2.3 第 1 条：两台的所选、科目映射不同，每分钟轮流报，连接配置与版本号都不动，排队的单不会被作废。"""
        before = (self.doc.config_json, self.doc.record_revision, self.doc.projection_heads)
        other = row(r"S:\2569\EXP69\OTHER", r"S:\2569\EXP69", uid="v1:0002", share=r"\\srv\pas\2569\EXP69\OTHER")
        for minute in range(3):
            reply_a = self.a.report(body(self.S_BOOK, other, selected=r"S:\2569\EXP69\BOOK",
                                         mapping={"revenue_acc": "4100"}), NOW + timedelta(minutes=minute))
            reply_b = self.b.report(body(self.Z_BOOK, selected=r"Z:\2569\EXP69\BOOK", mapping={"revenue_acc": "4999"}),
                                    NOW + timedelta(minutes=minute))
        self.assertEqual((self.doc.config_json, self.doc.record_revision), before[:2])
        # 回复里的所选是每台自己上次报的
        self.assertEqual((reply_a["account_set"], reply_b["account_set"]),
                         (r"S:\2569\EXP69\BOOK", r"Z:\2569\EXP69\BOOK"))
        self.assertEqual((self.a.state["revenue_acc"], self.b.state["revenue_acc"]), ("4100", "4999"))
        # 目录：内容没变就不出新版本
        revisions = {r.projection_revision for r in DB.rows("Cowork ERP Snapshot") if r.scope_kind == "endpoint"}
        self.assertEqual(len(revisions), 1)

    def test_an_empty_list_clears_only_that_computer(self):
        self.a.report(body(self.S_BOOK), NOW)
        self.b.report(body(self.Z_BOOK), NOW)
        self.b.report(body(), NOW + timedelta(minutes=1))  # B 上的 Express 坏了 / 卸了
        self.assertEqual(list(union()), [self.KEY])
        self.assertEqual(set(reach()), {("CCD-A", self.KEY)})
        heads = state.heads(self.doc)
        self.assertFalse(any(h.get("status") == "error" for h in heads.values()))
        # 名单没变的心跳：不改可达行、不重发目录
        published = len(DB.rows("Cowork ERP Snapshot"))
        self.a.report(body(unchanged=True), NOW + timedelta(minutes=2))
        self.assertEqual(len(DB.rows("Cowork ERP Snapshot")), published)

    def test_writable_is_any_computer_that_can_write(self):
        read_only = row(r"Z:\2569\EXP69\BOOK", r"Z:\2569\EXP69", uid="v1:0001", share=r"\\srv\pas\2569\EXP69\BOOK",
                        writable=False)
        self.b.report(body(read_only), NOW)
        self.assertFalse(union()[self.KEY]["attributes"]["writable"])
        self.a.report(body(self.S_BOOK), NOW)
        self.assertTrue(union()[self.KEY]["attributes"]["writable"])

    def test_migrating_computers_wait_then_join_the_old_key(self):
        """过渡期：旧电脑 A 还是旧版（只报路径），新电脑 B 报了文件号 → B 待认定、不拆第二个键；A 升级后报上同一文件号
        → B 认上同一份，规范键仍是今天的路径（不变量）。"""
        legacy_a = fake.Computer(self.doc, "CCD-A", credential="KA", legacy=True, version="1.5.1")
        old = row(r"S:\2569\EXP69\BOOK", r"S:\2569\EXP69", **TAX)
        legacy_a.report(body(old), NOW)
        self.b.report(body(self.Z_BOOK), NOW)
        key = "s:\\2569\\exp69\\book"
        self.assertEqual(reach()[("CCD-B", key)].state, "pending_identity")
        self.assertEqual(list(union()), [key])
        self.assertFalse(pool.reaches("CONN", "CCD-B", key))
        # A 升级（认领，新版报文件号与网络路径）
        self.a.report(body(self.S_BOOK), NOW + timedelta(minutes=1))
        self.assertEqual(reach()[("CCD-B", key)].state, "ok")
        registration = DB.rows(pool.ACCOUNT_SET)[0]
        self.assertEqual((registration.account_key, registration.root_key, registration.uid),
                         (key, "s:\\2569\\exp69", "v1:0001"))
        self.assertEqual(len(DB.rows(pool.ACCOUNT_SET)), 1)
        self.assertEqual(pool.local_paths("CONN", "CCD-B"), {key: r"Z:\2569\EXP69\BOOK"})

    def test_pending_ends_after_seven_days_or_when_the_old_computer_is_removed(self):
        legacy_a = fake.Computer(self.doc, "CCD-A", credential="KA", legacy=True, version="1.5.1")
        legacy_a.report(body(row(r"S:\2569\EXP69\BOOK", r"S:\2569\EXP69", **TAX)), NOW)
        self.b.report(body(self.Z_BOOK), NOW)
        self.b.report(body(unchanged=True), NOW + timedelta(days=6))
        self.assertEqual(reach()[("CCD-B", "s:\\2569\\exp69\\book")].state, "pending_identity")
        self.b.report(body(unchanged=True), NOW + timedelta(days=7, minutes=1))
        self.assertEqual(reach("CCD-B")[("CCD-B", self.KEY)].state, "ok")
        self.assertEqual(set(union()), {"s:\\2569\\exp69\\book", self.KEY})
        # 被移除：同样不再等
        DB.clear()
        self.setUp()
        legacy_a = fake.Computer(self.doc, "CCD-A", credential="KA", legacy=True, version="1.5.1")
        legacy_a.report(body(row(r"S:\2569\EXP69\BOOK", r"S:\2569\EXP69", **TAX)), NOW)
        self.b.report(body(self.Z_BOOK), NOW)
        pool.forget_device("CCD-A")
        self.b.report(body(unchanged=True), NOW + timedelta(minutes=1))
        self.assertEqual(set(reach()), {("CCD-B", self.KEY)})
        self.assertEqual(list(union()), [self.KEY])

    def test_canonical_key_and_root_never_change(self):
        legacy_a = fake.Computer(self.doc, "CCD-A", credential="KA", legacy=True, version="1.5.1")
        legacy_a.report(body(row(r"S:\2569\EXP69\BOOK", r"S:\2569\EXP69")), NOW)
        record = DB.rows(pool.ACCOUNT_SET)[0]
        # 同一台换了映射盘符、换了程序目录：只改可达行，登记不变
        self.a.report(body(row(r"S:\2569\EXP69\BOOK", r"Q:\NEWROOT", uid="u1", share=r"\\srv\pas\2569\EXP69\BOOK")),
                      NOW + timedelta(minutes=1))
        record = frappe.get_doc(pool.ACCOUNT_SET, record.name)
        self.assertEqual((record.account_key, record.root_key), ("s:\\2569\\exp69\\book", "s:\\2569\\exp69"))
        record.account_key = "s:\\elsewhere"
        with self.assertRaises(fake.PermissionError_):
            record.save()
        record = frappe.get_doc(pool.ACCOUNT_SET, record.name)
        record.root_key = "q:\\newroot"
        with self.assertRaises(fake.PermissionError_):
            record.save()
        # 补丁按推送记录补的、规范根为空的登记：第一台够得到它的电脑补上一次
        pool.ensure_registered(self.doc, r"D:\GONE\BOOK", now=NOW)
        legacy_a.report(body(row(r"D:\GONE\BOOK", r"D:\GONE")), NOW + timedelta(minutes=2))
        self.assertEqual(DB.docs[pool.ACCOUNT_SET][pool.registration_name("CONN", "d:\\gone\\book")].root_key,
                         "d:\\gone")


class Facts(Base):
    def test_masters_are_kept_per_account_set_whichever_computer_read_them(self):
        book = row(r"S:\X\BOOK", r"S:\X", uid="u1", share=r"\\srv\s\X\BOOK")
        self.a.report(body(book, selected=r"S:\X\BOOK", accounts=[{"code": "1100"}]), NOW)
        self.b.report(body(row(r"Z:\X\BOOK", r"Z:\X", uid="u1"), selected=r"Z:\X\BOOK",
                           catalog={"products": [{"code": "P1"}], "customers": []}), NOW)
        facts = pool.facts("CONN", r"\\srv\s\x\book")
        self.assertEqual([a["code"] for a in facts["reported_accounts"]], ["1100"])  # 合并：B 没带科目表，留 A 的
        self.assertEqual(facts["reported_products"], [{"code": "P1"}])
        self.assertEqual((facts["_device"], facts["_routine"]), ("CCD-B", True))
        self.assertNotIn("reported_accounts", model.parse(self.doc.report_json))

    def test_twenty_per_connection_and_open_pushes_keep_theirs(self):
        for i in range(21):
            pool.put_facts(self.doc, rf"S:\X\B{i:02d}", {"reported_accounts": [{"code": str(i)}]}, device="CCD-A",
                           routine=False, now=NOW + timedelta(minutes=i), protected=lambda: {r"s:\x\b00"})
        keys = {r.account_key for r in DB.rows(pool.FACTS)}
        self.assertEqual(len(keys), 20)
        self.assertIn(r"s:\x\b00", keys)  # 最久没读，但有未了结的推送记录引用
        self.assertNotIn(r"s:\x\b01", keys)
        self.assertIn(r"s:\x\b20", keys)

    def test_unchanged_masters_are_not_rewritten_every_minute(self):
        self.assertTrue(pool.put_facts(self.doc, r"S:\X\B", {"reported_accounts": [{"code": "1"}]}, device="CCD-A",
                                       routine=True, now=NOW))
        self.assertFalse(pool.put_facts(self.doc, r"S:\X\B", {"reported_accounts": [{"code": "1"}]}, device="CCD-A",
                                        routine=True, now=NOW + timedelta(minutes=5)))
        self.assertTrue(pool.put_facts(self.doc, r"S:\X\B", {"reported_accounts": [{"code": "1"}]}, device="CCD-A",
                                       routine=True, now=NOW + timedelta(minutes=11)))

    def test_new_stock_masters_are_added_to_the_account_sets_facts(self):
        self.a.report(body(row(r"S:\X\B", r"S:\X", uid="u1"), selected=r"S:\X\B",
                           catalog={"products": [], "customers": [], "fingerprint": {"stock_master_count": 4}}), NOW)
        key = r"\\acc-01\s$\x\b"
        result = stock_effects.apply("CONN", {"account_set": key, "root_key": r"\\acc-01\s$\x"}, 2)
        self.assertEqual((result["state"], result["before"], result["after"]), ("applied", 4, 6))
        self.assertEqual(stock_effects.apply("CONN", {"account_set": key, "root_key": r"D:\OTHER"}, 1)["state"],
                         "target_changed")
        self.assertEqual(stock_effects.apply("CONN", {"account_set": r"S:\NONE", "root_key": ""}, 1)["state"],
                         "target_changed")

    def test_preparation_evidence_v2_names_the_account_set_not_a_computer(self):
        """证据 v2（第 3 批）：不绑任何电脑或密钥，只认（连接，规范键）；主档取这份账套自己的资料（哪台读的都一样），
        不含版本号（载荷版本在领取时按领取的电脑判）。电脑来领时只核登记仍在与本单用到的那几行。"""
        book_a = row(r"C:\EXP\BOOK", r"C:\EXP", uid="a:1")
        self.a.report(body(book_a, selected=r"C:\EXP\BOOK", accounts=[{"code": "1100"}, {"code": "2100"}],
                           max_payload_version=2), NOW)
        self.b.report(body(row(r"C:\EXP\BOOK", r"C:\EXP", uid="b:1")), NOW)  # B 后报：与证据无关
        loaded = types.SimpleNamespace(doc=types.SimpleNamespace(name="CONN"), revision=1,
                                       access=types.SimpleNamespace(user_id="owner@example.com"))

        def target(key, root):
            return types.SimpleNamespace(account_set=key, root_key=root, endpoint_id="CONN", user_id="owner@example.com",
                                         tenant_id="testcowork.pearnly.com", binding_generation=0)

        key = r"\\acc-01\c$\exp\book"
        facts, evidence = preparation.collect(loaded, target(key, r"\\acc-01\c$\exp"))
        self.assertEqual((evidence["version"], evidence["route"]), (2, {"connection": "CONN", "account_key": key}))
        self.assertNotIn("credential", evidence)
        self.assertEqual(facts["reported_config"]["reported_accounts"], [{"code": "1100"}, {"code": "2100"}])
        self.assertNotIn("max_payload_version", facts["reported_config"])
        used = lambda facts: [row for row in facts["reported_config"].get("reported_accounts") or [] if row["code"] == "1100"]
        evidence["basis_hash"] = preparation.basis(used(facts))
        preparation.revalidate(loaded, target(key, r"\\acc-01\c$\exp"), evidence)  # 人确认：整份相等（不看 basis_hash）
        preparation.revalidate(loaded, target(key, r"\\acc-01\c$\exp"), evidence, mode="lease", used=used)
        # 本单没用到的科目变了：领取照常；人确认（整份）不成立
        self.a.report(body(book_a, selected=r"C:\EXP\BOOK", accounts=[{"code": "1100"}, {"code": "2100", "name": "x"}]),
                      NOW + timedelta(minutes=1))
        preparation.revalidate(loaded, target(key, r"\\acc-01\c$\exp"), evidence, mode="lease", used=used)
        with self.assertRaises(fake.ValidationError):
            preparation.revalidate(loaded, target(key, r"\\acc-01\c$\exp"), evidence)
        # 本单用到的那一行变了：领取不成立（照旧转人工）
        self.a.report(body(book_a, selected=r"C:\EXP\BOOK", accounts=[{"code": "1100", "name": "renamed"}]),
                      NOW + timedelta(minutes=2))
        with self.assertRaises(fake.ValidationError):
            preparation.revalidate(loaded, target(key, r"\\acc-01\c$\exp"), evidence, mode="lease", used=used)
        # 没有 used（调用方漏传）一律不成立，不放行
        with self.assertRaises(fake.ValidationError):
            preparation.revalidate(loaded, target(key, r"\\acc-01\c$\exp"), evidence, mode="lease")

    def test_account_master_view_reads_the_pool(self):
        self.a.report(body(row(r"S:\X\B", r"S:\X", uid="u1"), selected=r"S:\X\B", accounts=[{"code": "1"}]), NOW)
        key = r"\\acc-01\s$\x\b"
        view = account_masters.view(self.doc, key, NOW + timedelta(minutes=5))
        self.assertEqual((view["state"], view["selected"], view["request"]), ("ready", True, None))
        self.assertEqual(account_masters.view(self.doc, r"S:\NOT\IN\POOL", NOW)["state"], "unavailable")


class Dispatch(Base):
    """第 3 批（方案 2.5、2.10）：分派要的池里几样——这台电脑可写的账套与本机写法、池里能写的电脑、写成后记在登记上
    （旧版近似黏住）、写前文件号对不上标 stale（直到整份上报）、账套主档按池里够得到它的电脑判在不在线 / 要不要升级。"""

    S_BOOK = row(r"S:\2569\BOOK", r"S:\2569", uid="v1:1", share=r"\\srv\pas\2569\BOOK", **TAX)
    Z_BOOK = row(r"Z:\2569\BOOK", r"Z:\2569", uid="v1:1", share=r"\\srv\pas\2569\BOOK", **TAX)
    KEY = r"\\srv\pas\2569\book"

    def test_each_computer_writes_with_its_own_spelling(self):
        self.a.report(body(self.S_BOOK), NOW)
        self.b.report(body({**self.Z_BOOK, "attributes": {**self.Z_BOOK["attributes"], "writable": False}}), NOW)
        self.assertEqual(pool.writable_reach("CONN", "CCD-A")[self.KEY].path, r"S:\2569\BOOK")
        self.assertEqual(pool.writable_reach("CONN", "CCD-B"), {})  # 只读够得到的不派写入
        self.assertEqual(pool.writer_devices("CONN"), {"CCD-A"})
        self.assertEqual(pool.reachers("CONN", self.KEY, writable=True), ["CCD-A"])

    def test_reachers_and_registrations_come_back_in_one_query_for_many_account_sets(self):
        # 第 3 批审查：领取时连接行锁着，不逐份账套查
        other = r"\\srv\pas\2569\other"
        self.a.report(body(self.S_BOOK, row(r"S:\2569\OTHER", r"S:\2569", uid="v1:2", share=r"\\srv\pas\2569\OTHER")), NOW)
        self.b.report(body({**self.Z_BOOK, "attributes": {**self.Z_BOOK["attributes"], "writable": False}}), NOW)
        self.assertEqual(pool.reachers_of("CONN", [self.KEY, other, r"\\srv\none"], writable=True),
                         {self.KEY: ["CCD-A"], other: ["CCD-A"], r"\\srv\none": []})
        self.assertEqual(pool.reachers_of("CONN", [self.KEY])[self.KEY], ["CCD-A", "CCD-B"])
        pool.note_written("CONN", self.KEY, "CCD-A", NOW)
        found = pool.registrations_of("CONN", [self.KEY, other, r"\\srv\none"])
        self.assertEqual(sorted(found), sorted([self.KEY, other]))
        self.assertEqual((found[self.KEY].uid, found[self.KEY].last_writer_device), ("v1:1", "CCD-A"))
        self.assertEqual(found[self.KEY].content_key, identity.content_key(TAX))
        self.assertEqual((pool.reachers_of("CONN", []), pool.registrations_of("CONN", [])), ({}, {}))

    def test_entry_marks_are_kept_on_the_registration_but_never_change_the_directory(self):
        """第 3 期 E3（CC 桌面版 1.9.1）：每行账套另报录入标记（GLJNL.DBF 表头的日期 / 记录数）：两处白名单都收，存在登记上
        给重问读（一次主键查询），不进池目录——标记每天随整套重扫变，目录不因它出新版本；不报这个键的电脑（旧版）不抹掉
        别的电脑报的。"""
        self.assertIn("entry_mark", _ACCOUNT_SET_KEYS)
        marked = {**self.S_BOOK, "attributes": {**self.S_BOOK["attributes"], "entry_mark": "2026-10-19/120"}}
        self.a.report(body(marked), NOW)
        self.assertNotIn("entry_mark", union()[self.KEY]["attributes"])
        self.assertEqual(pool.entered_of("CONN", [self.KEY, r"\\srv\none"]), {self.KEY: "2026-10-19/120"})
        published = len(DB.rows("Cowork ERP Snapshot"))
        later = {**marked, "attributes": {**marked["attributes"], "entry_mark": "2026-10-20/121"}}
        self.a.report(body(later), NOW + timedelta(minutes=1))
        self.assertEqual(len(DB.rows("Cowork ERP Snapshot")), published)
        self.assertEqual(pool.entered_of("CONN", [self.KEY]), {self.KEY: "2026-10-20/121"})
        self.b.report(body(self.Z_BOOK), NOW + timedelta(minutes=2))   # 旧版 CC 桌面版不报：保留上一份
        self.assertEqual(pool.entered_of("CONN", [self.KEY]), {self.KEY: "2026-10-20/121"})
        self.assertEqual((pool.entered_of("CONN", []), pool.entered_of("", [self.KEY])), ({}, {}))

    def test_a_writer_needs_a_live_key_whose_issuer_may_still_use_the_connection(self):
        # 第 3 批审查：确认（`has_writer`）与池的状态同一个判据
        self.a.report(body(self.S_BOOK), NOW)
        self.assertEqual(pool.writers(self.doc, self.KEY), ["CCD-A"])
        self.assertEqual(pool.writers(self.doc), ["CCD-A"])
        fake.PRESENCE["CONN"]["CCD-A"]["online"] = False
        self.assertEqual(pool.writers(self.doc, self.KEY), ["CCD-A"])  # 关着也算（D4）
        fake.LOST_ACCESS.add("owner@example.com")  # 签发人被移出使用名单
        self.assertEqual(pool.writers(self.doc, self.KEY), [])
        fake.LOST_ACCESS.clear()
        del fake.PRESENCE["CONN"]["CCD-A"]  # 密钥吊销 / 电脑退役：不在 presence 里
        self.assertEqual(pool.writers(self.doc, self.KEY), [])
        self.assertEqual(pool.writers(self.doc, r"\\srv\none", peers={"CCD-A": {"issuers": {"owner@example.com"}}}), [])

    def test_the_computers_doing_someones_work_are_his_own_or_the_folder_reader(self):
        # 用户 10-16（ADR-021 第 50–53 条）：`owned` 筛出替这件活干的电脑；`writers` 只在里面找
        peers = {"CCD-A": {"users": {"me@x.com"}, "issuers": {"owner@example.com"}},
                 "CCD-B": {"users": {"other@x.com", "me@x.com"}, "issuers": {"owner@example.com"}},
                 "CCD-C": {"users": frozenset(), "issuers": {"owner@example.com"}},
                 "CCD-D": {"users": {"other@x.com"}, "issuers": {"owner@example.com"}}}
        self.assertEqual(sorted(pool.owned(peers, "me@x.com")), ["CCD-A", "CCD-B", "CCD-C"])  # C 没有登录记录：过渡期算
        self.assertEqual(sorted(pool.owned(peers, "other@x.com")), ["CCD-B", "CCD-C", "CCD-D"])
        self.assertEqual(list(pool.owned(peers, "me@x.com", device="CCD-D")), ["CCD-D"])  # 定时任务：只有那台
        self.assertEqual(pool.owned(peers, "me@x.com", device="CCD-GONE"), {})
        self.assertEqual(pool.owned({}, "me@x.com"), {})
        self.a.report(body(self.S_BOOK), NOW)
        self.assertEqual(pool.writers(self.doc, self.KEY, peers=pool.owned(
            {"CCD-A": {"users": {"me@x.com"}, "issuers": {"owner@example.com"}}}, "other@x.com")), [])

    def test_a_write_is_noted_on_the_registration_and_a_uid_mismatch_goes_stale_until_a_full_report(self):
        self.a.report(body(self.S_BOOK), NOW)
        self.assertTrue(pool.note_written("CONN", self.KEY, "CCD-A", NOW))
        found = pool.registration("CONN", self.KEY)
        self.assertEqual((found.last_writer_device, found.last_written_at), ("CCD-A", NOW))
        self.assertFalse(pool.note_written("CONN", r"\\srv\none", "CCD-A", NOW))
        self.assertTrue(pool.mark_stale("CONN", "CCD-A", self.KEY))
        self.assertFalse(pool.mark_stale("CONN", "CCD-A", self.KEY))  # 已经是 stale
        self.assertEqual(pool.writable_reach("CONN", "CCD-A"), {})
        self.assertEqual(pool.reachers("CONN", self.KEY), [])
        self.assertIn(self.KEY, union())  # 目录里照旧列着（只是这台暂时不写，别的电脑照样能写）
        self.a.report(body(self.S_BOOK), NOW + timedelta(minutes=1))  # 整份上报：恢复
        self.assertEqual(list(pool.writable_reach("CONN", "CCD-A")), [self.KEY])

    def test_account_masters_follow_any_computer_of_the_pool(self):
        self.a.report(body(self.S_BOOK, selected=r"S:\2569\BOOK", accounts=[{"code": "1"}]), NOW)
        self.b.report(body(self.Z_BOOK), NOW)
        fake.PRESENCE["CONN"]["CCD-A"]["online"] = False  # A 关机，B 在线：照样「可用」
        view = account_masters.view(self.doc, self.KEY, NOW + timedelta(minutes=1))
        self.assertEqual(view["state"], "ready")
        fake.PRESENCE["CONN"]["CCD-B"]["online"] = False
        self.assertEqual(account_masters.view(self.doc, self.KEY, NOW + timedelta(minutes=1))["state"], "offline")
        # 另一份账套：池里有一台 CC 桌面版读得了别的账套，就不算「要升级」
        other = r"\\srv\pas\2569\other"
        self.b.report(body(self.Z_BOOK, row(r"Z:\2569\OTHER", r"Z:\2569", uid="v1:2", share=r"\\srv\pas\2569\OTHER")),
                      NOW + timedelta(minutes=2))
        fake.PRESENCE["CONN"]["CCD-B"]["version"] = "1.2.4"
        self.assertEqual(account_masters.view(self.doc, other, NOW + timedelta(minutes=2))["state"], "update_needed")
        fake.PRESENCE["CONN"]["CCD-B"]["version"] = "1.6.0"
        self.assertEqual(account_masters.view(self.doc, other, NOW + timedelta(minutes=2))["state"], "reading")


class Refresh(Base):
    BOOK_S = row(r"S:\X\BOOK", r"S:\X", uid="u1", share=r"\\srv\s\X\BOOK")
    BOOK_Z = row(r"Z:\X\BOOK", r"Z:\X", uid="u1", share=r"\\srv\s\X\BOOK")
    KEY = r"\\srv\s\x\book"

    def setUp(self):
        super().setUp()
        self.a.report(body(self.BOOK_S, selected=r"S:\X\OTHER"), NOW)
        self.b.report(body(self.BOOK_Z, selected=r"Z:\X\OTHER"), NOW)

    def request(self, key=None, actor="x@example.com", at=NOW):
        kind, scope_key, head_key = state.scope(key, "express")
        name = "R-%d" % len(DB.rows("Cowork ERP Refresh"))
        refresh.create(self.doc, kind, scope_key, head_key, actor=actor, identity=name, signature="s", now=at)
        return name

    def test_an_account_set_read_goes_to_the_first_computer_that_reaches_it(self):
        # 用户 10-16（ADR-021 第 54 条）：账套主档是这条连接的公共资料，够得到它的电脑谁先来谁读，没有档位、没有 120 秒
        self.assertFalse(hasattr(refresh, "HANDOFF_SECONDS") or hasattr(refresh, "my_turn") or hasattr(refresh, "tier"))
        self.request(self.KEY, actor="x@example.com")
        leased = self.b.lease(NOW + timedelta(seconds=5))
        self.assertEqual(leased["account_set_key"], r"z:\x\book")  # B 自己的写法
        self.assertIsNone(self.a.lease(NOW + timedelta(seconds=6)))  # 已被 B 领走
        # B 读完用它自己的写法交回，服务端换回规范键
        self.b.report(body(self.BOOK_Z, selected=r"Z:\X\OTHER", accounts=[{"code": "9"}],
                           catalog={"products": [], "customers": []}, master_refresh_request_id=leased["request_id"],
                           master_refresh_scope="account_set", master_refresh_account_set=r"Z:\X\BOOK"),
                      NOW + timedelta(seconds=30))
        done = [r for r in DB.rows("Cowork ERP Refresh") if r.status == "succeeded"]
        self.assertEqual(len(done), 1)
        self.assertEqual(pool.facts("CONN", self.KEY)["reported_accounts"], [{"code": "9"}])

    def test_an_account_set_read_lease_belongs_to_its_computer(self):
        self.request(self.KEY)
        leased = self.a.lease(NOW + timedelta(seconds=6))
        self.assertEqual(leased["account_set_key"], r"s:\x\book")  # A 自己的写法
        # 别的电脑来报到不判 A 的租约失败；同一台换了密钥才判
        self.b.report(body(unchanged=True), NOW + timedelta(seconds=7))
        name = next(r.name for r in DB.rows("Cowork ERP Refresh") if r.lease_handle == leased["request_id"])
        self.assertEqual(DB.docs["Cowork ERP Refresh"][name].status, "leased")
        self.a.credential = "KA2"
        self.a.report(body(unchanged=True), NOW + timedelta(seconds=8))
        row_ = DB.docs["Cowork ERP Refresh"][name]
        self.assertEqual((row_.status, row_.error_code), ("failed", "ERR_AGENT_CREDENTIAL_CHANGED"))

    def test_a_whole_directory_read_gives_each_computer_its_own_child_lease(self):
        parent = self.request()
        a = self.a.lease(NOW + timedelta(seconds=1))
        b = self.b.lease(NOW + timedelta(seconds=2))
        self.assertEqual((a["scope_kind"], b["scope_kind"]), ("endpoint", "endpoint"))
        self.assertNotEqual(a["request_id"], b["request_id"])
        self.assertIsNone(self.a.lease(NOW + timedelta(seconds=3)))  # 每台一份
        self.a.report(body(self.BOOK_S, master_refresh_request_id=a["request_id"], master_refresh_scope="endpoint"),
                      NOW + timedelta(seconds=10))
        self.assertEqual(DB.docs["Cowork ERP Refresh"][parent].status, "leased")  # B 还没交回
        self.b.report(body(self.BOOK_Z, row(r"Z:\X\NEW", r"Z:\X", uid="u2"), master_refresh_request_id=b["request_id"],
                           master_refresh_scope="endpoint"), NOW + timedelta(seconds=11))
        main = DB.docs["Cowork ERP Refresh"][parent]
        self.assertEqual(main.status, "succeeded")
        _, _, key = state.scope(None)
        self.assertEqual(main.result_revision, state.heads(self.doc)[key]["revision"])
        self.assertIn(r"\\acc-02\z$\x\new", union())

    def test_computers_that_do_not_join_in_time_do_not_hold_the_directory_read(self):
        parent = self.request()
        a = self.a.lease(NOW + timedelta(seconds=1))
        self.a.report(body(self.BOOK_S, master_refresh_request_id=a["request_id"], master_refresh_scope="endpoint"),
                      NOW + timedelta(seconds=10))
        self.assertEqual(DB.docs["Cowork ERP Refresh"][parent].status, "leased")  # B 是池里的，等它一会儿
        self.assertIsNone(self.b.lease(NOW + timedelta(seconds=refresh.JOIN_SECONDS + 5)))  # 晚了不再发子租约
        self.assertEqual(DB.docs["Cowork ERP Refresh"][parent].status, "succeeded")

    def test_one_computer_completes_a_directory_read_at_once(self):
        DB.clear()
        doc = connection("SOLO")
        solo = fake.Computer(doc, "CCD-S", legacy=True)
        solo.report(body(row(r"C:\EXP\BOOK", r"C:\EXP")), NOW)
        kind, key, head = state.scope(None)
        refresh.create(doc, kind, key, head, actor="x", identity="R-SOLO", signature="s", now=NOW)
        leased = solo.lease(NOW + timedelta(seconds=1))
        solo.report(body(row(r"C:\EXP\BOOK", r"C:\EXP"), master_refresh_request_id=leased["request_id"],
                         master_refresh_scope="endpoint"), NOW + timedelta(seconds=2))
        self.assertEqual(DB.docs["Cowork ERP Refresh"]["R-SOLO"].status, "succeeded")

    def test_a_directory_read_fails_only_when_no_computer_succeeded(self):
        parent = self.request()
        a = self.a.lease(NOW + timedelta(seconds=1))
        self.b.lease(NOW + timedelta(seconds=2))
        self.a.report(body(master_refresh_request_id=a["request_id"], master_refresh_scope="endpoint",
                           master_refresh_error="ERR_TARGET_CATALOG_SCAN_FAILED"), NOW + timedelta(seconds=3))
        self.assertEqual(DB.docs["Cowork ERP Refresh"][parent].status, "leased")
        refresh.expire(self.doc, NOW + timedelta(seconds=refresh.LEASE_SECONDS + 5))
        main = DB.docs["Cowork ERP Refresh"][parent]
        self.assertEqual((main.status, main.error_code), ("failed", "ERR_TARGET_CATALOG_SCAN_FAILED"))


class ReviewBatch2(Base):
    """第 2 批对抗审查的复现（`f87a0585..d99c1b5b`）：每条先复现、再守住修法。"""

    def legacy(self, device="CCD-L", credential="KL"):
        return fake.Computer(self.doc, device, credential=credential, legacy=True, version="1.5.1")

    def test_1_a_key_moved_to_the_same_computers_claimed_record_keeps_the_canonical_key(self):
        """MR.ERP 成员先 device_hello，Express 的旧槽位密钥改挂到那台电脑、旧电脑退役：可达行与本机登记先转过去，
        那台第一次整份上报（带文件号）按第 3 条认回今天的键，不登记第二个。"""
        self.legacy().report(body(row(r"S:\EXP\BOOK", r"S:\EXP", **TAX)), NOW)
        self.assertEqual(pool.move_device("CONN", "CCD-L", "CCD-T"), 1)
        pool.forget_device("CCD-L")  # devices.retire_orphan：转完再退役
        self.assertEqual(DB.rows(pool.ACCOUNT_SET)[0].scope_device, "CCD-T")
        moved = fake.Computer(self.doc, "CCD-T", credential="KL", legacy=False, host="acc-01", aliases=ALIASES)
        moved.report(body(row(r"S:\EXP\BOOK", r"S:\EXP", uid="v9:77", share=r"\\nas\pas\EXP\BOOK", **TAX)),
                     NOW + timedelta(minutes=1))
        registrations = DB.rows(pool.ACCOUNT_SET)
        self.assertEqual([(r.account_key, r.root_key, r.uid) for r in registrations],
                         [("s:\\exp\\book", "s:\\exp", "v9:77")])
        self.assertEqual(list(union()), ["s:\\exp\\book"])
        self.assertEqual(reach()[("CCD-T", "s:\\exp\\book")].state, "ok")

    def test_1_moving_replaces_a_pending_row_waiting_for_the_old_computer(self):
        """先 register 后 hello（superseded）：那台已经整份报过、在等旧电脑这份 → 转过去的就是它。"""
        self.legacy().report(body(row(r"S:\EXP\BOOK", r"S:\EXP", **TAX)), NOW)
        self.a.report(body(row(r"S:\EXP\BOOK", r"S:\EXP", uid="v9:77", share=r"\\nas\pas\EXP\BOOK", **TAX)), NOW)
        self.assertEqual(reach()[("CCD-A", "s:\\exp\\book")].state, "pending_identity")
        pool.move_device("CONN", "CCD-L", "CCD-A")
        pool.forget_device("CCD-L")
        self.assertEqual(reach()[("CCD-A", "s:\\exp\\book")].state, "ok")
        self.a.report(body(row(r"S:\EXP\BOOK", r"S:\EXP", uid="v9:77", share=r"\\nas\pas\EXP\BOOK", **TAX)),
                      NOW + timedelta(minutes=1))
        self.assertEqual([r.account_key for r in DB.rows(pool.ACCOUNT_SET)], ["s:\\exp\\book"])

    def test_2_a_remapped_drive_never_rebinds_a_registration(self):
        x, y = {"tax_id": "1", "period_start": "2026-01-01"}, {"tax_id": "2", "period_start": "2026-01-01"}
        self.a.report(body(row(r"S:\EXP\BOOK", r"S:\EXP", uid="aa:1", share=r"\\nas1\exp\book", **x)), NOW)
        self.a.report(body(row(r"S:\EXP\BOOK", r"S:\EXP", uid="bb:2", share=r"\\nas2\exp\book", **y)),
                      NOW + timedelta(minutes=1))
        registrations = {r.account_key: r for r in DB.rows(pool.ACCOUNT_SET)}
        first = registrations[r"\\nas1\exp\book"]
        self.assertEqual((first.uid, first.location, first.content_key),
                         ("aa:1", r"\\nas1\exp\book", identity.content_key(x)))
        self.assertIn(r"\\nas2\exp\book", registrations)
        self.assertEqual(set(reach()), {("CCD-A", r"\\nas2\exp\book")})

    def test_2_another_share_spelling_of_the_same_folder_never_rewrites_the_location(self):
        self.a.report(body(row(r"S:\EXP\BOOK", r"S:\EXP", uid="aa:1", share=r"\\nas\pas\exp\book", **TAX)), NOW)
        self.b.report(body(row(r"Z:\BOOK", r"Z:", uid="aa:1", share=r"\\nas\exp\book", **TAX)), NOW)
        record = DB.rows(pool.ACCOUNT_SET)
        self.assertEqual([(r.account_key, r.location) for r in record], [(r"\\nas\pas\exp\book", r"\\nas\pas\exp\book")])

    def test_3_cloned_disks_keep_their_own_copies_apart(self):
        self.a.report(body(row(r"C:\EXP\BOOK", r"C:\EXP", uid="0c9a53f6:1234", **TAX)), NOW)
        self.b.report(body(row(r"C:\EXP\BOOK", r"C:\EXP", uid="0c9a53f6:1234", **TAX)), NOW)
        self.assertEqual(sorted(union()), [r"\\acc-01\c$\exp\book", r"\\acc-02\c$\exp\book"])
        self.assertFalse(pool.reaches("CONN", "CCD-B", r"\\acc-01\c$\exp\book"))

    def test_4_two_nas_behind_the_same_ip_are_never_merged(self):
        x, y = {"tax_id": "1", "period_start": "2026-01-01"}, {"tax_id": "2", "period_start": "2026-01-01"}
        place = r"\\192.168.1.10\exp\book"
        self.a.report(body(row(r"S:\EXP\BOOK", r"S:\EXP", uid="aa:1", share=place, **x)), NOW)
        self.b.report(body(row(r"S:\EXP\BOOK", r"S:\EXP", uid="bb:2", share=place, **y)), NOW)
        registration = DB.rows(pool.ACCOUNT_SET)
        self.assertEqual([(r.account_key, r.uid) for r in registration], [(place, "aa:1")])
        self.assertEqual(reach()[("CCD-B", place)].state, "pending_identity")
        self.assertFalse(pool.reaches("CONN", "CCD-B", place))
        self.assertEqual(pool.reachers("CONN", place), ["CCD-A"])

    def test_5_an_unknown_spelling_never_borrows_another_account_sets_key(self):
        self.legacy().report(body(row(r"C:\EXP\BOOK", r"C:\EXP", **TAX)), NOW)
        self.b.report(body(row(r"C:\EXP\BOOK", r"C:\EXP", uid="b:1", **TAX), selected=r"C:\EXP\BOOK",
                           accounts=[{"code": "1111"}]), NOW)
        self.assertEqual(reach()[("CCD-B", "c:\\exp\\book")].state, "pending_identity")
        self.assertIsNone(pool.facts("CONN", "c:\\exp\\book"))
        self.assertEqual(pool.canonical("CONN", "CCD-B", r"C:\EXP\BOOK"), "")
        self.assertEqual(pool.canonical("CONN", "CCD-L", r"C:\EXP\BOOK"), "c:\\exp\\book")
        # 「所选」认不出：那份账套的读请求不按第 1 档交给 B（B 也不够得到它）
        kind, key, head = state.scope("c:\\exp\\book", "express")
        refresh.create(self.doc, kind, key, head, actor="x@example.com", identity="R-1", signature="s", now=NOW)
        self.assertIsNone(self.b.lease(NOW + timedelta(seconds=1)))
        # 按账套读失败、写法认不出：不把整个目录标错
        self.b.report({"companion_version": "1.6.0", "account_sets_unchanged": True,
                       "projection_error": {"scope": "account_set", "account_set": r"C:\EXP\BOOK",
                                            "error_code": "ERR_X"}}, NOW + timedelta(minutes=1))
        self.assertFalse(any(h.get("status") == "error" for h in state.heads(self.doc).values()))

    def test_6_a_union_over_the_limit_is_cut_not_failed(self):
        saved = pool.MAX_ACCOUNT_SETS
        pool.MAX_ACCOUNT_SETS = 3
        try:
            self.a.report(body(*[row(r"\\srv\a\B%d" % i, r"\\srv\a", uid="a:%d" % i, share=r"\\srv\a\B%d" % i)
                                 for i in range(2)]), NOW)
            local = row(r"C:\EXP\LOCAL", r"C:\EXP", uid="b:9")
            read_only = row(r"\\srv\b\RO", r"\\srv\b", uid="b:8", share=r"\\srv\b\RO", writable=False)
            self.b.report(body(local, read_only, row(r"\\srv\b\B0", r"\\srv\b", uid="b:0", share=r"\\srv\b\B0")),
                          NOW)
        finally:
            pool.MAX_ACCOUNT_SETS = saved
        self.assertEqual(sorted(union()), [r"\\srv\a\b0", r"\\srv\a\b1", r"\\srv\b\b0"])
        self.assertEqual(state.heads(self.doc)[state.scope(None)[2]]["account_sets_dropped"], 2)
        self.assertTrue(any(r.device == "CCD-B" for r in DB.rows(pool.REACH)))  # B 的心跳没有回滚

    def test_8_an_empty_root_frozen_by_an_open_push_is_not_filled(self):
        pool.ensure_registered(self.doc, r"S:\EXP\BOOK", root="", now=NOW)
        self.legacy().report(body(row(r"S:\EXP\BOOK", r"S:\EXP")), NOW, protected=lambda: {r"S:\EXP\BOOK"})
        self.assertEqual(DB.rows(pool.ACCOUNT_SET)[0].root_key, "")
        self.legacy().report(body(row(r"S:\EXP\BOOK", r"S:\EXP")), NOW + timedelta(minutes=1), protected=lambda: set())
        self.assertEqual(DB.rows(pool.ACCOUNT_SET)[0].root_key, "s:\\exp")

    def test_9_forgetting_one_connection_keeps_the_others(self):
        other = connection("CONN2")
        fake.Computer(other, "CCD-A", credential="KA2", legacy=False, host="acc-01", aliases=ALIASES).report(
            body(row(r"S:\X\B", r"S:\X", uid="u2", share=r"\\srv\x\b")), NOW)
        self.a.report(body(row(r"S:\Y\B", r"S:\Y", uid="u1", share=r"\\srv\y\b")), NOW)
        pool.forget_device("CCD-A", connection="CONN")
        self.assertEqual({(r.connection, r.device) for r in DB.rows(pool.REACH)}, {("CONN2", "CCD-A")})
        self.assertEqual(pool.reachers("CONN", r"\\srv\y\b"), [])


# ── 整份上报批量写（CC 教程问题第 10 条：679 份首次上报逐行写，持连接行锁 9–10 秒）──────────────────────────


def many(count, *, drive="S", legacy=False, volume=0x5A, relabel=(), skip=(), extra=0, mark="2026-10-20/100"):
    """一台电脑报的一大份名单：每份一个目录文件号与网络路径（``legacy`` 时都没有，同 1.7.0 之前的旧版）。"""
    rows = []
    for index in [*range(count), *range(10_000, 10_000 + extra)]:
        if index in skip:
            continue
        folder = "%d\\EXP%02d\\C%05d" % (2560 + index % 10, index % 100, index)
        identified = {} if legacy else {"uid": "v1:%x:%05d" % (volume, index), "share": "\\\\srv\\pas\\" + folder,
                                        "entry_mark": mark}
        rows.append(row(drive + ":\\" + folder, drive + ":\\" + folder.rsplit("\\", 1)[0],
                        name="Company %d%s" % (index, " (renamed)" if index in relabel else ""),
                        tax_id="0105559%06d" % index, branch="00000", period_start="2026-01-01", **identified))
    return rows


def network_key(item):
    return identity.normalize_location(item["attributes"]["share"], aliases=ALIASES)


class RegistryIndex(unittest.TestCase):
    """`targets.identity.Registry`：建一次、各行共用的登记索引，答案与逐份按登记顺序扫一样。"""

    LOCATIONS = ["", r"\\ACC-01\exp\a", r"\\192.168.1.5\EXP\A", r"\\acc-02\exp\a"]

    def registered(self, seed):
        import random

        chance = random.Random(seed)
        return [R("k%02d" % index, uid=chance.choice(["", "u1", "u2", "u3"]), location=chance.choice(self.LOCATIONS),
                  content_key=chance.choice(["", "tax:1", "tax:2"]), foreign=chance.random() < 0.3,
                  others_live=chance.random() < 0.5) for index in range(60)]

    @staticmethod
    def scan(items, key, accept):
        """原来 `resolve` 里逐份扫的条件（第 1、2、5 条）。"""
        for item in items:
            if key[0] == "uid" and item.uid and item.uid == key[1] and accept(item):
                return item
            if (key[0] == "location" and item.location
                    and identity.normalize_location(item.location, aliases=ALIASES) == key[1]
                    and item.content_key == key[2] and accept(item)):
                return item
            if key[0] == "content" and item.content_key == key[1] and accept(item):
                return item
        return None

    @staticmethod
    def probes():
        for uid in ("u1", "u2", "u3", "u4"):
            yield ("uid", uid), (lambda item: True)
            yield ("uid", uid), (lambda item: not item.foreign)
        for location in (r"\\acc-01\exp\a", r"\\acc-02\exp\a", r"\\acc-03\exp\a"):
            for content in ("", "tax:1", "tax:2"):
                yield ("location", location, content), (lambda item: True)
        for content in ("tax:1", "tax:2", "tax:3"):
            yield ("content", content), (lambda item: not item.identified and item.others_live)

    def test_first_match_in_registration_order_like_a_linear_scan(self):
        for seed in range(25):
            items = self.registered(seed)
            index = identity.Registry(items, aliases=ALIASES)
            for key, accept in self.probes():
                self.assertEqual(index.first(key, accept), self.scan(items, key, accept), (seed, key))
            # 本轮里补上文件号 / 网络路径（原位替换）、新登记（追加在最后）：索引跟着换，答案仍同逐份扫
            items[7] = items[7]._replace(uid="u4", location=r"\\acc-03\exp\a")
            items.append(R("new", uid="u1", location=r"\\acc-01\exp\a", content_key="tax:1"))
            index.put(items[7])
            index.put(items[-1])
            for key, accept in self.probes():
                self.assertEqual(index.first(key, accept), self.scan(items, key, accept), (seed, key, "after put"))
            self.assertEqual([item.account_key for item in index], [item.account_key for item in items])

    def test_resolve_gives_the_same_decision_with_a_list_or_a_shared_registry(self):
        registered = self.registered(3) + [R(r"\\acc-01\pas\book", "r", uid="v1:0001", location=r"\\acc-01\pas\book",
                                             content_key=identity.content_key(TAX))]
        index = identity.Registry(registered, aliases=ALIASES)
        rows = [row(r"S:\BOOK", uid="v1:0001"), row(r"S:\BOOK", share=r"\\192.168.1.5\pas\book", **TAX),
                row(r"C:\EXP\BOOK", uid="u2"), row(r"C:\EXP\BOOK"), row(r"Z:\X", uid="zz", **TAX)]
        for item in rows:
            for legacy in (True, False):
                self.assertEqual(decide(item, registered, legacy=legacy),
                                 resolve(item, registered=index, own_reach={}, legacy=legacy, host="acc-02",
                                         aliases=ALIASES))


class BatchedReport(Base):
    """整份上报（`pool.apply_report`）与重核（`pool.reconsider`）：开头加锁读一次、认定用一份索引、写入攒到最后批量写。"""

    COUNT = 679
    CHUNKS = [200, 200, 200, 79]

    def setUp(self):
        super().setUp()
        self.statements, self.document_writes = [], []
        original_sql, original_write = DB.sql, pool.access.write

        def sql(query, values=(), as_dict=False):
            self.statements.append(" ".join(query.split()))
            return original_sql(query, values, as_dict)

        def write(doc):
            self.document_writes.append(doc["doctype"])
            return original_write(doc)

        DB.sql = sql
        pool.access.write = write
        self.addCleanup(setattr, DB, "sql", original_sql)
        self.addCleanup(setattr, pool.access, "write", original_write)

    def reset(self):
        self.statements.clear()
        self.document_writes.clear()
        DB.bulk_inserts.clear()
        DB.updates.clear()
        DB.locks.clear()

    def pool_document_writes(self):
        return [doctype for doctype in self.document_writes if doctype in (pool.ACCOUNT_SET, pool.REACH)]

    def pool_statements(self, *prefixes):
        return [q for q in self.statements if q.startswith(prefixes) and (pool.ACCOUNT_SET in q or pool.REACH in q)]

    def batches(self, doctype, kind):
        return [count for table, count in getattr(DB, kind) if table == doctype]

    def test_a_first_full_report_inserts_each_table_in_a_few_statements_in_report_order(self):
        rows = many(self.COUNT)
        self.a.report(body(*rows), NOW)
        reads = self.pool_statements("SELECT")
        self.assertEqual(self.batches(pool.ACCOUNT_SET, "bulk_inserts"), self.CHUNKS)
        self.assertEqual(self.batches(pool.REACH, "bulk_inserts"), self.CHUNKS)
        self.assertEqual(self.pool_document_writes(), [])  # 一次逐行的文档写入都没有
        # 登记、可达与目录照上报顺序（``ORDER BY creation, name``），同逐行插入
        keys = [network_key(item) for item in rows]
        self.assertEqual([r.account_key for r in DB.rows(pool.ACCOUNT_SET)], keys)
        self.assertEqual([r.account_key for r in DB.rows(pool.REACH)], keys)
        self.assertEqual([item["source_id"] for item in pool.catalog("CONN")], keys)
        self.assertEqual(set(union()), set(keys))
        # 锁区间：登记与可达整表加锁读，次数与只报 3 份时相同（不随份数涨）；不再逐行 ``get_doc(for_update=True)``
        self.assertTrue(all(q.endswith("FOR UPDATE") for q in reads
                            if q.startswith(("SELECT name, account_key, root_key", "SELECT name, device, account_key"))))
        self.assertFalse([lock for lock in DB.locks if lock[0] in (pool.ACCOUNT_SET, pool.REACH)])
        self.reset()
        small = fake.Computer(connection("CONN2"), "CCD-A", credential="KA2", legacy=False, host="acc-01",
                              aliases=ALIASES)
        small.report(body(*many(3)), NOW)
        self.assertEqual(len(self.pool_statements("SELECT")), len(reads))
        self.assertEqual(len(self.pool_statements("INSERT")), 2)

    def test_the_same_list_again_writes_nothing(self):
        rows = many(self.COUNT)
        self.a.report(body(*rows), NOW)
        self.reset()
        self.a.report(body(*rows), NOW + timedelta(minutes=1))
        self.assertEqual((DB.bulk_inserts, DB.updates, self.pool_document_writes()), ([], [], []))
        self.assertEqual(self.pool_statements("INSERT", "UPDATE", "DELETE"), [])
        # 隔一天：可达行只补记报到时间，一组列一条语句
        self.reset()
        self.a.report(body(*rows), NOW + timedelta(days=1, minutes=2))
        self.assertEqual(DB.updates, [(pool.REACH, count) for count in self.CHUNKS])
        self.assertEqual({r.seen_at for r in DB.rows(pool.REACH)}, {NOW + timedelta(days=1, minutes=2)})

    def test_entry_marks_changing_every_day_are_one_statement_per_chunk(self):
        self.a.report(body(*many(self.COUNT)), NOW)
        self.reset()
        self.a.report(body(*many(self.COUNT, mark="2026-10-21/101")), NOW + timedelta(minutes=1))
        self.assertEqual(DB.updates, [(pool.ACCOUNT_SET, count) for count in self.CHUNKS])
        self.assertEqual({json.loads(r.attributes_json)["entry_mark"] for r in DB.rows(pool.ACCOUNT_SET)},
                         {"2026-10-21/101"})
        self.assertEqual(self.pool_document_writes(), [])

    def test_a_partial_change_writes_only_what_changed_and_drops_what_is_gone(self):
        rows = many(self.COUNT)
        self.a.report(body(*rows), NOW)
        dropped = set(range(5, self.COUNT, 20))
        renamed = set(range(0, self.COUNT, 10)) - dropped
        self.reset()
        self.a.report(body(*many(self.COUNT, relabel=renamed, skip=dropped, extra=5)), NOW + timedelta(minutes=1))
        self.assertEqual(sum(self.batches(pool.ACCOUNT_SET, "updates")), len(renamed))
        self.assertEqual(DB.bulk_inserts, [(pool.ACCOUNT_SET, 5), (pool.REACH, 5)])
        self.assertEqual(self.pool_document_writes(), [])
        gone = {network_key(rows[index]) for index in dropped}
        self.assertFalse(gone & {r.account_key for r in DB.rows(pool.REACH)})  # 名单里没了：这台的可达行删掉
        self.assertTrue(gone <= {r.account_key for r in DB.rows(pool.ACCOUNT_SET)})  # 登记留着
        self.assertFalse(gone & set(union()))
        self.assertEqual(len(union()), self.COUNT - len(dropped) + 5)
        labels = {r.account_key: r.label for r in DB.rows(pool.ACCOUNT_SET)}
        self.assertEqual(labels[network_key(rows[0])], "Company 0 (renamed)")
        # 报了空名单：只清这台的可达行
        self.reset()
        self.a.report(body(), NOW + timedelta(minutes=2))
        self.assertFalse(DB.rows(pool.REACH))
        self.assertEqual((DB.bulk_inserts, DB.updates, self.pool_document_writes()), ([], [], []))

    def test_two_computers_reporting_the_same_list_in_turn_share_the_registrations(self):
        self.a.report(body(*many(self.COUNT)), NOW)
        seen_a = {r.name: r.seen_at for r in DB.rows(pool.REACH)}
        self.reset()
        self.b.report(body(*many(self.COUNT, drive="Z")), NOW + timedelta(minutes=1))
        self.assertEqual(len(DB.rows(pool.ACCOUNT_SET)), self.COUNT)
        self.assertEqual(DB.bulk_inserts, [(pool.REACH, count) for count in self.CHUNKS])
        self.assertEqual(self.pool_document_writes(), [])
        # B 的整份不碰 A 的可达行；轮流再报，内容没变就谁也不写
        self.assertEqual({r.name: r.seen_at for r in DB.rows(pool.REACH) if r.device == "CCD-A"}, seen_a)
        for minute in (2, 3):
            self.reset()
            self.a.report(body(*many(self.COUNT)), NOW + timedelta(minutes=minute))
            self.b.report(body(*many(self.COUNT, drive="Z")), NOW + timedelta(minutes=minute))
            self.assertEqual((DB.bulk_inserts, DB.updates), ([], []))
        first = many(1)[0]
        self.assertEqual(pool.local_paths("CONN", "CCD-B")[network_key(first)], r"Z:\2560\EXP00\C00000")

    def test_a_whole_list_waiting_for_an_old_computer_is_identified_in_one_heartbeat(self):
        """旧版电脑先登记（没有文件号），新版电脑报同一批 → 整份待认定；每次心跳重核、仍待认定时一行不写；旧版升级后带
        文件号整份重报 → 另一台等着的整份在这一次心跳里认上，写入仍是批量的。"""
        legacy = fake.Computer(self.doc, "CCD-A", credential="KA", legacy=True, version="1.5.1")
        legacy.report(body(*many(self.COUNT, legacy=True)), NOW)
        self.b.report(body(*many(self.COUNT, drive="Z", volume=0x6B)), NOW)
        self.assertEqual({r.state for r in DB.rows(pool.REACH) if r.device == "CCD-B"}, {"pending_identity"})
        self.reset()
        self.b.report(body(unchanged=True), NOW + timedelta(minutes=1))
        self.assertEqual((DB.bulk_inserts, DB.updates, self.pool_document_writes()), ([], [], []))
        self.reset()
        self.a.report(body(*many(self.COUNT, volume=0x6B)), NOW + timedelta(minutes=2))
        self.assertEqual({r.state for r in DB.rows(pool.REACH)}, {"ok"})
        self.assertEqual(len(DB.rows(pool.ACCOUNT_SET)), self.COUNT)  # 规范键仍是旧版登记的路径，不拆第二个键
        self.assertEqual({r.account_key for r in DB.rows(pool.REACH) if r.device == "CCD-B"},
                         {r.account_key for r in DB.rows(pool.ACCOUNT_SET)})
        self.assertEqual(self.pool_document_writes(), [])
        # 登记改列、A 的可达改列、B 认定后的可达插入、B 认上时登记的显示属性：每类按 200 行一批
        self.assertLessEqual(len(DB.bulk_inserts) + len(DB.updates), 4 * len(self.CHUNKS))
        self.assertEqual(pool.local_paths("CONN", "CCD-B")["s:\\2560\\exp00\\c00000"], r"Z:\2560\EXP00\C00000")

    def test_rows_are_checked_like_a_document_write(self):
        meta = frappe.get_meta(pool.REACH)
        self.assertEqual(pool._valid(meta, {"registry_row": None, "path": "x"}), {"registry_row": 0, "path": "x"})
        with self.assertRaises(fake.ValidationError):
            pool._valid(meta, {"device": "d" * 141})  # 文字超长报错，不悄悄截断
        with self.assertRaises(fake.ValidationError):
            pool._valid(meta, {"not_a_column": 1})
        # 规范根只从空补一次（同 ``CoworkExpressAccountSet.validate``）：批量改列照样过控制器的校验
        self.a.report(body(*many(3)), NOW)
        name = DB.rows(pool.ACCOUNT_SET)[0].name
        with self.assertRaises(fake.PermissionError_):
            DB.sql(f"UPDATE `tab{pool.ACCOUNT_SET}` SET `root_key`=CASE name WHEN %s THEN %s END, `modified`=%s, "
                   f"`modified_by`=%s WHERE name IN %s", (name, "q:\\elsewhere", NOW, "x", (name,)))


class NoDefaultAccountSet(unittest.TestCase):
    """方案 2.3 第 7 条：池里没有连接级默认账套。Express 读到的配置去掉旧心跳写下的所选与 6 个科目，数据原样留着。"""

    def test_pool_config_strips_only_express_defaults(self):
        config = {"account_set": r"S:\A", "account_dir": r"S:\A", "express_root": "S:", "account_company": "A",
                  "account_set_row": 3, "revenue_acc": "4100", "directions": "purchase", "autonomy": "manual"}
        self.assertEqual(model.pool_config("express", model.dumps(config)), {"directions": "purchase",
                                                                             "autonomy": "manual"})
        self.assertEqual(model.pool_config("mrerp", config), config)

    def test_without_a_default_an_express_target_must_be_in_the_directory(self):
        endpoint = {"id": "CONN", "adapter": "express", "enabled": True,
                    "config": model.pool_config("express", {"account_set": r"S:\OLD", "express_root": "S:"})}
        self.assertIsNone(resolve_account_choice(endpoint, tenant_id="t", user_id="u"))
        with self.assertRaises(TargetSelectionError):
            require_catalog_evidence(endpoint, tenant_id="t", user_id="u", account_set_key=r"S:\OLD",
                                     snapshot={"snapshot_id": "S", "head_snapshot_id": "S", "adapter": "express",
                                               "account_sets": []})


# ── 补丁 v0_0_20 ─────────────────────────────────────────────────────────────────────


class Seed(unittest.TestCase):
    BINDING = {"account_set": r"s:\2569\exp69\default", "root_key": r"s:\2569\exp69", "credential_id": "KA",
               "generation": 1, "issuer": "owner@example.com"}

    def setUp(self):
        DB.clear()
        self.doc = connection(report_credential="KA", report_generation=1)
        sets = [{"source_id": r"s:\2569\exp69\default", "label": "Default",
                 "attributes": {"path": r"S:\2569\EXP69\DEFAULT", "root": r"S:\2569\EXP69", "writable": True,
                                "company": "Default Co", "tax_id": "1"}},
                {"source_id": r"s:\2569\exp69\other", "label": "Other",
                 "attributes": {"path": r"S:\2569\EXP69\OTHER", "root": r"S:\2569\EXP69", "writable": True}}]
        DB.docs["Cowork ERP Snapshot"] = {
            "SNAP-E": Doc(doctype="Cowork ERP Snapshot", name="SNAP-E", connection="CONN", connection_revision=1,
                          scope_kind="endpoint", scope_key="@endpoint", body=model.dumps({"account_sets": sets})),
            "SNAP-O": Doc(doctype="Cowork ERP Snapshot", name="SNAP-O", connection="CONN", connection_revision=1,
                          scope_kind="account_set", scope_key=r"s:\2569\exp69\other", observed_at=NOW,
                          body=model.dumps({"account_sets": sets, "reported": {
                              "reported_accounts": [{"code": "5300"}], "_credential": "KA", "_generation": 1}}))}
        heads = {state.scope(None)[2]: {"snapshot_id": "SNAP-E", "revision": 3, "status": "fresh"},
                 state.scope(r"s:\2569\exp69\other", "express")[2]: {"snapshot_id": "SNAP-O", "revision": 1,
                                                                    "status": "fresh"}}
        self.doc.projection_heads = model.dumps(heads)
        origin = dict(self.BINDING)
        self.doc.report_json = model.dumps({
            "_preparation_binding": {**self.BINDING, "online": True},
            "_preparation_origins": {"reported_accounts": origin, "reported_products": None},
            "reported_accounts": [{"code": "1100"}], "reported_products": [{"code": "NOT-ATTRIBUTED"}],
            "reported_account_sets": [{"code": "EXTRA", "path": r"S:\2569\EXP69\EXTRA", "root": r"S:\2569\EXP69",
                                       "writable": True}],
            "_account_caches": {r"s:\2568\old": {"reported_accounts": [{"code": "9"}]}},
            "companion_version": "1.5.1"})
        self.before = (self.doc.config_json, self.doc.record_revision, self.doc.projection_heads)

    def seed(self, **kwargs):
        kwargs.setdefault("device", "CCD-A")
        kwargs.setdefault("referenced", {r"d:\gone\book": r"D:\GONE"})
        return pool.seed_legacy("CONN", now=NOW, **kwargs)

    def test_todays_keys_and_roots_become_the_canonical_ones(self):
        self.assertEqual(self.seed(), {"registrations": 5, "facts": 3})
        registrations = {r.account_key: r for r in DB.rows(pool.ACCOUNT_SET)}
        self.assertEqual(set(registrations), {r"s:\2569\exp69\default", r"s:\2569\exp69\other", r"s:\2569\exp69\extra",
                                              r"s:\old\default", r"d:\gone\book"})
        self.assertEqual(registrations[r"s:\2569\exp69\default"].root_key, r"s:\2569\exp69")
        self.assertEqual(registrations[r"s:\2569\exp69\default"].path, r"S:\2569\EXP69\DEFAULT")  # 显示原样
        self.assertEqual(registrations[r"d:\gone\book"].root_key, r"d:\gone")
        # 旧电脑的可达行（引用到、目录里已经没有的键没有）
        self.assertEqual({key for _, key in reach("CCD-A")},
                         set(registrations) - {r"d:\gone\book"})
        self.assertEqual(reach()[("CCD-A", r"s:\2569\exp69\default")].path, r"S:\2569\EXP69\DEFAULT")
        # 账套资料：默认账套那份只搬记在它名下的键；旧缓存与按账套读回的各进各的
        self.assertEqual(pool.facts("CONN", r"s:\2569\exp69\default")["reported_accounts"], [{"code": "1100"}])
        self.assertNotIn("reported_products", pool.facts("CONN", r"s:\2569\exp69\default"))
        self.assertTrue(pool.facts("CONN", r"s:\2569\exp69\default")["_routine"])
        self.assertEqual(pool.facts("CONN", r"s:\2569\exp69\other")["reported_accounts"], [{"code": "5300"}])
        self.assertEqual(pool.facts("CONN", r"s:\2568\old")["reported_accounts"], [{"code": "9"}])
        # 不动：连接配置、版本号、目录快照指针
        self.assertEqual((self.doc.config_json, self.doc.record_revision, self.doc.projection_heads), self.before)

    def test_running_again_changes_nothing(self):
        self.seed()
        snapshot = {doctype: {name: dict(row) for name, row in rows.items()} for doctype, rows in DB.docs.items()}
        self.assertEqual(self.seed(), {"registrations": 0, "facts": 0})
        self.assertEqual({doctype: {name: dict(row) for name, row in rows.items()}
                          for doctype, rows in DB.docs.items()}, snapshot)

    def test_a_computer_that_already_reported_is_not_seeded_from_the_old_directory(self):
        computer = fake.Computer(self.doc, "CCD-A", credential="KA", legacy=True)
        computer.report(body(row(r"S:\2569\EXP69\DEFAULT", r"S:\2569\EXP69")), NOW)
        self.seed()
        self.assertEqual(set(reach("CCD-A")), {("CCD-A", r"s:\2569\exp69\default")})
        self.assertIn(r"d:\gone\book", {r.account_key for r in DB.rows(pool.ACCOUNT_SET)})

    def test_without_an_old_computer_only_registrations(self):
        self.seed(device=None)
        self.assertEqual(reach(), {})
        self.assertEqual(len(DB.rows(pool.ACCOUNT_SET)), 5)

    def test_the_old_computer_reports_its_migrated_keys_unchanged(self):
        """补丁之后旧版照常心跳：按路径认回同一批登记（第 4 条），不新建、目录与补丁前同一批键。"""
        self.seed()
        legacy = fake.Computer(self.doc, "CCD-A", credential="KA", legacy=True)
        legacy.report(body(row(r"S:\2569\EXP69\DEFAULT", r"S:\2569\EXP69"), row(r"S:\2569\EXP69\OTHER", r"S:\2569\EXP69")),
                      NOW + timedelta(minutes=1))
        self.assertEqual(len(DB.rows(pool.ACCOUNT_SET)), 5)
        self.assertEqual(sorted(union()), [r"s:\2569\exp69\default", r"s:\2569\exp69\other"])


class PatchModule(unittest.TestCase):
    """补丁本身：跨域的查询（旧电脑、未了结推送记录、启用的定时任务）在补丁里，搬数据的规则在 `pool.seed_legacy`。"""

    def load(self, answers):
        import importlib.util

        spec = importlib.util.spec_from_file_location("patch_v0_0_20", APP / "patches/v0_0_20_express_account_sets.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.frappe = types.SimpleNamespace(db=types.SimpleNamespace(
            sql=lambda query, values=(), as_dict=False: answers(" ".join(query.split()), values)))
        return module

    def test_old_computer_is_the_current_reporters(self):
        rows = [Doc(name="K1", device="D1"), Doc(name="K2", device="D2")]
        module = self.load(lambda q, values: rows)
        self.assertEqual(module._old_computer("CONN", "K2"), "D2")
        self.assertEqual(module._old_computer("CONN", "GONE"), "D1")
        self.assertIsNone(self.load(lambda q, values: [])._old_computer("CONN", "K1"))

    def test_referenced_keys_from_open_pushes_and_enabled_tasks(self):
        def answers(q, values):
            if "Push Record" in q:
                self.assertIn("NOT IN ('success', 'cancelled')", q)
                return [Doc(account_key=r"S:\A\B", target_json=json.dumps({"root_key": r"S:\A"})),
                        Doc(account_key="", target_json=json.dumps({"account_set": r"S:\A\C", "root_key": "S:\\A"}))]
            self.assertIn("enabled=1", q)
            return [Doc(account_set_key=r"S:\A\B"), Doc(account_set_key=r"T:\TASK")]
        self.assertEqual(self.load(answers)._referenced("CONN"), {r"s:\a\b": r"S:\A", r"s:\a\c": "S:\\A", r"t:\task": ""})

    def test_execute_seeds_every_express_pool_and_asks_for_one_full_report(self):
        calls, statements = [], []

        def answers(q, values):
            statements.append((q, values))
            if q.startswith("SELECT name, report_credential"):
                self.assertIn("adapter='express'", q)
                return [Doc(name="C1", report_credential="K1", config_json='{"account_set": "S:\\\\A"}',
                            report_json='{"companion_version": "1.5.1"}')]
            if "FROM `tabCowork Express Agent Credential`" in q and q.startswith("SELECT"):
                return [Doc(name="K1", device="D1")]
            return []

        module = self.load(answers)
        module.frappe.db.table_exists = lambda table: True
        module.frappe.db.has_column = lambda table, column: True
        stub = types.SimpleNamespace
        fake_pool = stub(seed_legacy=lambda connection, **kw: calls.append(("seed", connection, kw["device"],
                                                                            kw["protected"])))
        fake_devices = stub(seed_state=lambda device, connection, config, report: calls.append(
            ("state", device, connection, config, report)))
        saved = {name: sys.modules.get(name) for name in ("pearnly_cowork.connections.pool",
                                                          "pearnly_cowork.companion", "pearnly_cowork.companion.devices")}
        sys.modules.update({"pearnly_cowork.connections.pool": fake_pool,
                            "pearnly_cowork.companion": stub(devices=fake_devices),
                            "pearnly_cowork.companion.devices": fake_devices})
        sys.modules["pearnly_cowork.connections"].pool = fake_pool
        try:
            module.execute()
        finally:
            for name, value in saved.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value
            sys.modules["pearnly_cowork.connections"].pool = pool
        self.assertEqual(calls, [("seed", "C1", "D1", set()), ("state", "D1", "C1", {"account_set": "S:\\A"},
                                                                {"companion_version": "1.5.1"})])
        update = [item for item in statements if item[0].startswith("UPDATE")]
        self.assertEqual(len(update), 1)
        self.assertIn("SET full_report_due=1", update[0][0])
        self.assertEqual(update[0][1], ("C1",))

    def test_registered_after_the_device_patch(self):
        lines = (APP / "patches.txt").read_text().splitlines()
        self.assertLess(lines.index("pearnly_cowork.patches.v0_0_19_companion_devices"),
                        lines.index("pearnly_cowork.patches.v0_0_20_express_account_sets"))


class Schema(unittest.TestCase):
    def doctype(self, folder):
        return json.loads((APP / "pearnly_cowork/doctype" / folder / (folder + ".json")).read_text())

    def test_three_native_doctypes_guarded_by_the_connection_domain(self):
        hooks = (APP / "hooks.py").read_text()
        for folder, name in (("cowork_express_account_set", "Cowork Express Account Set"),
                             ("cowork_express_reach", "Cowork Express Reach"),
                             ("cowork_express_account_facts", "Cowork Express Account Facts")):
            doc = self.doctype(folder)
            fields = {f["fieldname"]: f for f in doc["fields"]}
            self.assertEqual(doc["name"], name)
            self.assertEqual(fields["record_key"]["unique"], 1)
            self.assertEqual(doc["field_order"], [f["fieldname"] for f in doc["fields"]])
            self.assertTrue(all(f.get("read_only") for f in doc["fields"]))
            self.assertIn(f'"{name}": "pearnly_cowork.connections.access.native_permission"', hooks)
            self.assertIn(f'"{name}": "pearnly_cowork.connections.access.native_query"', hooks)
            self.assertIn("ConnectionDocument", (APP / "pearnly_cowork/doctype" / folder / (folder + ".py")).read_text())
            self.assertNotIn("Companion", name)  # 用户可见的名字只叫 CC 桌面版，DocType 名不带旧称
        self.assertEqual(self.doctype("cowork_express_reach")["fields"][
            [f["fieldname"] for f in self.doctype("cowork_express_reach")["fields"]].index("state")]["options"],
            "ok\npending_identity\nstale")
        refresh_fields = {f["fieldname"] for f in self.doctype("cowork_erp_refresh")["fields"]}
        self.assertTrue({"lease_device", "parent_request"} <= refresh_fields)
        access_source = (APP / "connections/access.py").read_text()
        self.assertIn("POOL_TYPES", access_source)

    def test_connections_never_imports_companion(self):
        for path in (APP / "connections").glob("*.py"):
            text = path.read_text()
            self.assertNotIn("pearnly_cowork.companion", text, path.name)
            self.assertNotIn("from ..companion", text, path.name)


if __name__ == "__main__":
    unittest.main()
