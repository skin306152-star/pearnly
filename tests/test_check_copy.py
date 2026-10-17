"""界面文案闸门（scripts/check-copy.py）的守卫。

1. 每条判据至少一个坏例（必须拦 / 标黄）和一个好例（不能拦）。坏例取自真实语料：499d4417 改写前的说法、
   现行 cowork:498/501/509/2183/2199、core:111/128/600、control:33/41/45、cc-shell-rebuild 分支新增的
   「要人选」「还没推送」「单据较多，只统计了一部分」等；好例取自 499d4417 改写后的定稿说法。
   放宽 scripts/i18n/copy-words.tsv 漏掉已知坏例时这里变红 —— 想过闸只能改文案或逐条登记豁免。
2. 临时 git 仓库里测分类：新增、改动、译文改动（msgid 改写）、移动（换键不换字）、i18n.ts 重排不算改动；
   改已有文案未登记被拦、登记后放行；豁免生效、文案一变就失效；JSX 写死与翻译包新增行。
3. keep-cjk.tsv 里"规格："整文件豁免必须在 sources.TABLE_MODULES 或 NOT_COPY_SPEC 里登记。

只用标准库：python3 tests/test_check_copy.py（也能被 pytest 收集）。
"""

import argparse
import contextlib
import importlib.util
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from copygate import diff, policy, repo, rules, sources  # noqa: E402

_spec = importlib.util.spec_from_file_location("check_copy", SCRIPTS / "check-copy.py")
check_copy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_copy)

WORDS = rules.load_words(str(SCRIPTS / "i18n" / "copy-words.tsv"))
CHECKER = rules.Checker(WORDS, str(ROOT))
UPSTREAM = rules.load_upstream(str(SCRIPTS / "i18n" / "upstream-msgids.txt"))


def quiet_main(argv):
    """跑 check-copy 的入口，吞掉它的终端输出。"""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return check_copy.main(argv)


def entry(zh=None, en=None, th=None, ja=None, key="cowork.sample.text", source="pearnly_cowork", role=None,
          keyed=True, surface=None):
    texts = {k: v for k, v in (("zh", zh), ("en", en), ("th", th), ("ja", ja)) if v}
    item = sources.Entry(source, key, texts)
    item.extra["keyed"] = keyed
    item.role = role or rules.role_of(item, {}, WORDS)
    item.surface = surface or rules.surface_of(item, item.role)
    return item


def hits(item, level="block", checker=CHECKER, langs=sources.LANGS):
    return {f.rule for f in checker.check_entry(item, set(langs)) if f.level == level}


class RuleCorpus(unittest.TestCase):
    """每条判据：坏例拦、好例不拦。"""

    def assertBlocks(self, rule, item):
        self.assertIn(rule, hits(item), "%s 应拦：%s" % (rule, item.texts))

    def assertPasses(self, rule, item):
        self.assertNotIn(rule, hits(item), "%s 不该拦：%s" % (rule, item.texts))

    def test_len(self):
        self.assertBlocks("R-LEN", entry(zh="确认并保存当前全部修改过的内容", role="button"))
        self.assertPasses("R-LEN", entry(zh="保存", role="button"))
        # CC 网页的按钮、状态硬上限 10 字
        self.assertBlocks("R-LEN", entry(zh="单据较多仅统计部分内容", source="assistant", key="COMPANY.x.status", role="status"))
        self.assertIn("R-LEN", hits(entry(zh="同一产品的多个站点共用一个 Bench 进程，用于定位故障范围", role="hint"), "warn"))

    def test_sent(self):
        self.assertBlocks("R-SENT", entry(zh="暂无记录。请点击右上角「新建录入」。", role="title"))
        self.assertBlocks("R-SENT", entry(zh="此单据为转账凭条。本公司是否录入此类单据？选择后，同类单据将按此处理", role="error"))
        self.assertPasses("R-SENT", entry(zh="按物料编码匹配，未匹配时按条码匹配；重复导入的行将被覆盖。", role="hint"))
        self.assertPasses("R-SENT", entry(zh="未删除任何记录：选中的 {0} 条记录正在推送。", role="error"))

    def test_why(self):
        self.assertBlocks("R-WHY", entry(zh="就是该部署的域名。Frappe 按 Host 认站点，所以站点名与域名是同一个值", role="field_desc"))
        self.assertBlocks("R-WHY", entry(en="One per deployment: the callback URL is per site domain, so a shared client would lock the second tenant out."))
        self.assertBlocks("R-WHY", entry(zh="以下差异多为发票未读取所致，请稍后重新提交。"))
        self.assertPasses("R-WHY", entry(zh="与部署域名相同。", en="Same as the deployment domain.", role="field_desc"))
        self.assertPasses("R-WHY", entry(zh="为了您的账户安全，请登录后修改初始密码。", role="email"))

    def test_talk(self):
        bad = [
            "这条连接的电脑做不了这件事。",  # cowork:498
            "CC 桌面版还没做完。",  # cowork:501
            "CC 桌面版交回的数据读不了。",  # cowork:509
            "这里只能改付款方式和抵不抵进项",  # cowork:2183
            "这张单还没选推送去向",  # cowork:2199
            "要人选", "还没推送", "还没回读", "单据较多，只统计了一部分", "等你处理",  # cc-shell-rebuild
            "读不到要删除的内容", "这些账号删不掉：{0}",  # 499d4417 改写前
        ]
        for text in bad:
            self.assertBlocks("R-TALK", entry(zh=text))
        good = ["未选择推送目标", "需选择", "未推送", "仅统计部分单据", "无法读取待删除内容", "以下账号无法删除：{0}",
                "该租户不存在。", "确定作废此条记录？", "上一个", "下一个"]
        for text in good:
            self.assertPasses("R-TALK", entry(zh=text))
        self.assertBlocks("R-TALK", entry(th="ลองอีกครั้งนะครับ"))
        self.assertBlocks("R-TALK", entry(en="Just pick one and we'll do the rest"))
        self.assertPasses("R-TALK", entry(th="คะแนนความมั่นใจ"))
        self.assertPasses("R-TALK", entry(th="ค่าจ้างรายเดือน"))

    def test_we(self):
        self.assertBlocks("R-WE", entry(zh="我们会帮您重试"))
        self.assertBlocks("R-WE", entry(en="We keep it editable so switching instances later needs no code change."))
        self.assertPasses("R-WE", entry(zh="分配给我的"))
        self.assertPasses("R-WE", entry(en="Contact us"))

    def test_hint(self):
        self.assertBlocks("R-HINT", entry(zh="请注意：保存后无法修改"))
        self.assertBlocks("R-HINT", entry(en="Note: the read continues later"))
        self.assertPasses("R-HINT", entry(zh="备注", th="หมายเหตุ", role="label"))

    def test_narr(self):
        self.assertBlocks("R-NARR", entry(zh="DMS 连接中断，本次读取已暂停。系统将从断点继续读取，已完成的组仍保留。"))
        # 逗号连成的一句也算：≥ 2 个分句且还在讲"之后会怎样"
        self.assertBlocks("R-NARR", entry(zh="保存后，系统会自动推送并通知相关人员"))
        self.assertBlocks("R-NARR", entry(zh="识别结果会先保存在本地，推送成功后再同步到 Express"))
        # 已定稿的 DMS 出错句（10-13 G1–G8）：「系统会 / 将 + 读取 / 重试 / 继续」在出错角色只标黄
        dms = entry(zh="DMS 连接中断，本次读取已暂停。系统将从断点继续读取，已完成的组仍保留。",
                    key="dms.err.ERR_DMS_SNAPSHOT_RETRY", source="pearnly_dms")
        self.assertEqual(dms.role, "error")
        self.assertPasses("R-NARR", dms)
        self.assertIn("R-NARR", hits(dms, "warn"))
        self.assertPasses("R-NARR", entry(zh="登录服务暂时不可用，请稍后再试。", role="error"))
        single = entry(zh="写入失败，已撤回，将自动重试")
        self.assertPasses("R-NARR", single)
        self.assertIn("R-NARR", hits(single, "warn"))
        self.assertPasses("R-NARR", entry(zh="暂无数据"))

    def test_dev(self):
        self.assertBlocks("R-DEV", entry(en="The truth is in erp_push_logs; this is only a display reference"))
        self.assertBlocks("R-DEV", entry(zh="来自发号单。功能级强制暂未实施（见 docs/p4/06-provisioning.md）"))
        self.assertBlocks("R-DEV", entry(zh="缺少管理密钥（env PEARNLY_BIZ_ADMIN_SECRET）", source="pearnly_control", keyed=False))
        self.assertBlocks("R-DEV", entry(zh="用于 Webhook 验签。**每个租户独立配置**。", source="pearnly_control", keyed=False))
        self.assertBlocks("R-DEV", entry(en="Config is complete. Nothing was really connected — the adapter has not been ported yet"))
        self.assertBlocks("R-DEV", entry(zh="接口返回失败", source="assistant", key="COMPANY.loadFailed"))
        self.assertPasses("R-DEV", entry(zh="以逗号分隔。该角色将获得这些 DocType 的读写权限。", source="pearnly_control", keyed=False,
                                         role="field_desc"))
        # 弱术语：产品 Desk 标黄，CC 网页拦
        product = entry(zh="请填写必填字段")
        self.assertPasses("R-DEV", product)
        self.assertIn("R-DEV", hits(product, "warn"))
        self.assertBlocks("R-DEV", entry(zh="请填写必填字段", source="assistant", key="COMPANY.required"))
        # 强术语：管理后台只标黄
        admin = entry(zh="端点 {0} 未配置密码", source="pearnly_control", keyed=False)
        self.assertPasses("R-DEV", admin)
        self.assertIn("R-DEV", hits(admin, "warn"))
        # 术语登记：Channel Access Token 不算开发术语
        checker = rules.Checker(WORDS, str(ROOT), [("Channel Access Token", "")])
        token = entry(en="Channel Access Token", source="assistant", key="SETTINGS.token")
        self.assertIn("R-DEV", hits(token))
        self.assertNotIn("R-DEV", hits(token, checker=checker))

    def test_mix(self):
        self.assertIn("R-MIX", hits(entry(zh="请在 Foobar 中打开"), "warn"))
        self.assertNotIn("R-MIX", hits(entry(zh="请在 Express 中打开"), "warn"))

    def test_paren(self):
        self.assertBlocks("R-PAREN", entry(zh="两档都没有出结果（可能为网络、密钥或模型名称问题）"))
        self.assertBlocks("R-PAREN", entry(en="Ticked means on by default (for example CC Desktop)."))
        self.assertPasses("R-PAREN", entry(zh="导出 Excel（.xlsx, .csv）"))
        self.assertPasses("R-PAREN", entry(zh="开户凭据（存于此处）"))
        self.assertIn("R-PAREN", hits(entry(zh="对账结果（银行与总账逐笔核对）"), "warn"))

    def test_eg(self):
        self.assertBlocks("R-EG", entry(zh="金额，例如 5000", role="label"))
        self.assertPasses("R-EG", entry(zh="例如 5000", role="placeholder"))
        self.assertPasses("R-EG", entry(zh="请输入数字，例如 5000", role="error"))

    def test_hello(self):
        self.assertBlocks("R-HELLO", entry(zh="欢迎使用 Pearnly"))
        self.assertBlocks("R-HELLO", entry(zh="保存成功！"))
        self.assertBlocks("R-EMOJI", entry(zh="完成 🎉"))
        self.assertBlocks("R-HELLO", entry(th="สวัสดีค่ะ"))
        self.assertPasses("R-HELLO", entry(zh="感谢您选择 Pearnly。", role="email"))
        self.assertPasses("R-HELLO", entry(en="Welcome email", role="label"))
        self.assertPasses("R-HELLO", entry(zh="保存成功"))

    def test_empty(self):
        self.assertBlocks("R-EMPTY", entry(zh="暂无记录。请点击右上角「新建录入」。", key="intake.empty", source="pearnly_core"))
        self.assertBlocks("R-EMPTY", entry(zh="列表里空空如也", key="intake.empty"))
        for text in ("暂无记录", "购物车为空", "没有结果"):
            self.assertPasses("R-EMPTY", entry(zh=text, key="intake.empty"))
        self.assertPasses("R-EMPTY", entry(en="No results", key="search.empty"))
        # 键名里带 empty 不一定是空态：「空分组」是开关标签
        self.assertNotEqual(rules.role_of(entry(zh="空分组", key="SIDEBAR.emptyGroups", source="assistant"), {}), "empty")

    def test_name(self):
        self.assertBlocks("R-NAME", entry(zh="请先安装小助手"))
        self.assertPasses("R-NAME", entry(zh="请先安装 CC 桌面版"))

    def test_align(self):
        bad = entry(zh="与部署域名相同。", keyed=False, source="pearnly_control",
                    en="The domain of this deployment. Frappe picks the site by Host header, so site name and domain are the same value.")
        self.assertBlocks("R-ALIGN", bad)
        self.assertPasses("R-ALIGN", entry(zh="与部署域名相同。", en="Same as the deployment domain."))

    def test_line20(self):
        self.assertBlocks("R-LINE20", entry(th="อัปเดตข้อมูลลูกค้าในระบบ DMS", key="dms.card.BTN_UPDATE", source="pearnly_dms"))
        self.assertPasses("R-LINE20", entry(th="อัปเดตข้อมูล", key="dms.card.BTN_UPDATE", source="pearnly_dms"))

    def test_hard(self):
        snap = sources.Snapshot({}, {"Save this scheduled task?", "cowork.folder.day"}, {}, {}, {"send"}, {}, {}, {})

        def level(kind, text):
            found = rules.check_ref(sources.CodeRef(kind, text, "x", 1), snap, None, UPSTREAM)
            return found[0].level if found else None

        self.assertEqual(level("tsx-text", "Toggle Sidebar"), "block")
        self.assertEqual(level("tsx-attr", "aria-label=Toggle Sidebar"), "block")
        self.assertIsNone(level("tsx-text", "CC"))
        self.assertEqual(level("py-msgid", "Nothing was really connected yet"), "block")
        self.assertIsNone(level("py-msgid", "Not permitted"))  # Frappe 自带词（锁定镜像 main.pot）
        self.assertEqual(level("py-msgid", "Retrying"), "block")  # 不在术语表也不是 Frappe 自带词：短词也拦
        self.assertIsNone(level("tool-label", "Save this scheduled task?"))
        self.assertEqual(level("tool-label", "Save this task now please?"), "block")
        self.assertEqual(level("tsx-tr", "cowork.folder.nope"), "block")
        self.assertIsNone(level("tsx-tr", "cowork.folder.day"))
        self.assertEqual(level("tsx-t", "nope"), "block")
        self.assertIsNone(level("tsx-t", "send"))
        self.assertEqual(level("py-bare", "DMS snapshots are managed by the synchronizer"), "block")
        self.assertEqual(level("csv-manual", "Something new"), "warn")

    def test_frontend_refs(self):
        src = (
            "export function A() {\n"
            "  const cards: Record<string, ReactNode> = { company: x ? <Card /> : null,\n"
            "    month: <Month a={1} />,\n"
            "  };\n"
            "  return <div title=\"Open panel\" aria-label={t(\"send\")}>{tr(\"cowork.folder.day\")}<span>Toggle Sidebar</span></div>;\n"
            "}\n"
        )
        refs = {(r.kind, r.text) for r in sources.frontend_refs("frontend/assistant/src/a.tsx", src)}
        self.assertIn(("tsx-text", "Toggle Sidebar"), refs)
        self.assertIn(("tsx-attr", "title=Open panel"), refs)
        self.assertIn(("tsx-tr", "cowork.folder.day"), refs)
        self.assertIn(("tsx-t", "send"), refs)
        self.assertFalse([r for r in refs if "null" in r[1]], refs)


class KeepCjkRegistry(unittest.TestCase):
    def test_spec_whole_files_registered(self):
        """keep-cjk 里"规格："整文件豁免：给用户看的四语表进 TABLE_MODULES，不是界面文案的进 NOT_COPY_SPEC。"""
        missing = []
        for _, cols in rules.read_tsv_rows(str(SCRIPTS / "i18n" / "keep-cjk.tsv")):
            if len(cols) < 3 or cols[1] != "*" or not cols[2].startswith("规格"):
                continue
            path = cols[0]
            if path in sources.TABLE_MODULES or any(path.endswith(k) for k in sources.NOT_COPY_SPEC):
                continue
            missing.append(path)
        self.assertEqual(missing, [], "新的「规格：」整文件豁免要在 scripts/copygate/sources.py 登记")

    def test_table_modules_extract(self):
        for path in sources.TABLE_MODULES:
            self.assertTrue((ROOT / path).exists(), path)
        snap = sources.snapshot(repo.WorkTree(str(ROOT)))
        self.assertTrue(any(e.source == "table" for e in snap.entries.values()))


class FakeTree:
    """内存里的一棵树（paths / read / prefetch），给调用点角色用。"""

    def __init__(self, files):
        self.files = files

    def paths(self):
        return sorted(self.files)

    def read(self, path):
        return self.files.get(path)

    def prefetch(self, paths):
        return None


def refs_of(path, text):
    return {(r.kind, r.text) for r in sources.code_refs(path, text)}


def kinds_of(path, text):
    return {r.kind for r in sources.code_refs(path, text)}


class ReviewRound(unittest.TestCase):
    """评审第 1 轮逐条：误拦（官方写法、业务词、角色猜错）不拦，漏拦（绕开字典、换键改字、词表缺口）拦。"""

    def assertBlocks(self, rule, item):
        self.assertIn(rule, hits(item), "%s 应拦：%s" % (rule, item.texts))

    def assertPasses(self, rule, item):
        self.assertNotIn(rule, hits(item), "%s 不该拦：%s" % (rule, item.texts))

    # ── 误拦 ─────────────────────────────────────────────────────────────

    def test_why_follows_chinese(self):
        """英文 / 泰文的因果连接词以中文为准；中文本身解释原因照拦。"""
        same = entry(zh="资料未确认，无法推送到 DMS", en="The details are not confirmed yet, so it cannot be pushed to DMS",
                     source="pearnly_core")
        self.assertPasses("R-WHY", same)
        self.assertIn("R-WHY", hits(same, "warn"))
        busy = entry(zh="账套正被占用，已终止推送", th="ยกเลิกการส่งแล้วเนื่องจากชุดบัญชีกำลังถูกใช้งาน", key="intake.error.USER_ABORTED_BUSY")
        self.assertPasses("R-WHY", busy)
        self.assertBlocks("R-WHY", entry(en="Cannot delete {0} because it is linked to {1}", key="x.error"))  # 只有英文：英文就是权威
        self.assertBlocks("R-WHY", entry(zh="由于账套被锁，无法保存", en="Since the ledger is locked, changes can't be saved"))
        self.assertPasses("R-WHY", entry(en="Used when a new purchase line does not say otherwise", key="x.field_desc"))
        self.assertPasses("R-WHY", entry(en="Active since {0}", key="x.label"))
        self.assertBlocks("R-WHY", entry(en="Records are pushed one by one to avoid locking the ledger"))
        self.assertBlocks("R-WHY", entry(zh="为避免重复推送，同一单据只会推送一次"))

    def test_prompt_role(self):
        """CC 网页预填进输入框、由用户发出的话：不判口语，开发痕迹照判。"""
        for key, text in (("COMPANY.draftNotesText", "按这家的推送记录、记住的选择和 Express 历史起草几条知识库"),
                          ("COMPANY.handToCCOne", "处理这张：{x}"), ("COMPANY.ledger.duplicateText", "核对这几张同号单据：{x}")):
            item = entry(zh=text, key=key, source="assistant")
            self.assertEqual(item.role, "prompt", key)
            self.assertFalse(hits(item), key)
        self.assertBlocks("R-DEV", entry(zh="看一下 API 返回了什么", key="COMPANY.draftText", source="assistant"))
        self.assertBlocks("R-TALK", entry(zh="等你处理", key="COMPANY.chatWaiting", source="assistant"))

    def test_upstream_msgids(self):
        """沿用 Frappe / ERPNext 自带的说法不拦（锁定镜像的 main.pot 冻结在 upstream-msgids.txt）。"""
        snap = sources.Snapshot({}, set(), {}, {}, set(), {}, {}, {})
        for kind, text in (("desk-msgid", "Delete {0} items permanently?"), ("desk-msgid", "No results found"),
                           ("py-msgid", "About Us")):
            self.assertFalse(rules.check_ref(sources.CodeRef(kind, text, "x", 1), snap, None, UPSTREAM), text)
        self.assertTrue(rules.check_ref(sources.CodeRef("desk-msgid", "Delete {0} things for good?", "x", 1), snap, None, UPSTREAM))
        # tr() 走本仓术语表，Frappe 自带词不算
        self.assertTrue(rules.check_ref(sources.CodeRef("tsx-tr", "No results found", "x", 1), snap, None, UPSTREAM))
        self.assertGreater(len(UPSTREAM), 10000)

    def test_roles_from_keys(self):
        cases = {
            ("pearnly_erp_portal", "product.notice.nothingToSave", "There is nothing to save"): "unknown",
            ("pearnly_cowork", "erp.target_projection_duplicate_action", "ERP 报告包含重复的操作标识。"): "error",
            ("pearnly_cowork", "pos.no_header", "No header row"): "error",
            ("pearnly_cowork", "pos.column_missing", "Column {0} is missing"): "error",
            ("pearnly_cowork", "stocktake.code_as_text", "Product codes and barcodes must be stored as text."): "unknown",
            ("pearnly_core", "roster.code.step1", "1. Add your company’s LINE official account as a friend"): "hint",
            ("assistant", "MESSAGES.guideSafariMenu", "Tap ≡ at the left of the address bar, then Share"): "unknown",
            ("pearnly_cowork", "x._I18N.w_invoice_no_empty", "Invoice no. missing"): "status",
            ("pearnly_erp_portal", "trade.receiptsEmpty", "No receipts this shift"): "empty",
        }
        for (source, key, en), role in cases.items():
            self.assertEqual(entry(en=en, key=key, source=source).role, role, key)
        self.assertPasses("R-LEN", entry(en="There is nothing to save", key="product.notice.nothingToSave", source="pearnly_erp_portal"))
        self.assertEqual(entry(en="Save", key="cowork.push.save").role, "button")

    def test_roles_from_callsites(self):
        tree = FakeTree({
            "apps/pearnly_control/pearnly_control/onboarding/welcome.py":
                'frappe.sendmail(subject=translate("Welcome to {0} — your account is ready").format(x))\n',
            "frontend/src/pages/dms/RosterPage.vue": '<input :placeholder="t(\'roster.hint.displayName\')" />\n',
            "apps/pearnly_cowork/pearnly_cowork/x.py": 'frappe.throw(_("cowork.sample.code"))\n',
            "frontend/assistant/src/components/ui/drag.tsx": "screenReaderInstructions: { draggable: texts.instructions },\n",
        })
        sites = sources.callsite_roles(tree)
        welcome = sources.Entry("pearnly_control", "Welcome to {0} — your account is ready",
                                {"en": "Welcome to {0} — your account is ready", "zh": "欢迎使用 {0}，您的账户已开通"})
        welcome.extra["keyed"] = False
        self.assertEqual(rules.role_of(welcome, {}, WORDS, sites), "email")
        hint = sources.Entry("pearnly_core", "roster.hint.displayName", {"zh": "如：阿明（销售）"})
        hint.extra["keyed"] = True
        hint.role = rules.role_of(hint, {}, WORDS, sites)
        self.assertEqual(hint.role, "placeholder")
        hint.surface = "product"
        self.assertNotIn("R-EG", hits(hint))
        self.assertEqual(rules.role_of(entry(zh="编码有误", key="cowork.sample.code"), {}, WORDS, sites), "error")
        card = sources.Entry("assistant", "COMPANY.card.instructions", {"zh": "按空格键拿起卡片"})
        self.assertEqual(rules.role_of(card, {}, WORDS, sites), "sr")
        # 键名带 instructions、但没交给读屏的：换不来读屏的宽上限
        self.assertEqual(rules.role_of(sources.Entry("assistant", "COMPANY.fooInstructions", {"zh": "x"}), {}, WORDS, {}), "unknown")

    def test_thai_length(self):
        self.assertLess(rules.th_len("ลองสร้างใบจองอีกครั้ง"), len("ลองสร้างใบจองอีกครั้ง"))
        label = entry(zh="服务默认预扣税率（%）", th="อัตราภาษีหัก ณ ที่จ่ายเริ่มต้นสำหรับงานบริการ (%)", key="x.label")
        self.assertPasses("R-LEN", label)
        self.assertBlocks("R-LEN", entry(th="ก" * 130, key="x.error"))

    def test_empty_wording(self):
        for text in ("当班暂无小票", "缺少发票号"):
            self.assertPasses("R-EMPTY", entry(zh=text, key="trade.receiptsEmpty"))
        self.assertPasses("R-EMPTY", entry(th="ไม่พบข้อมูล", key="x.empty"))
        self.assertPasses("R-EMPTY", entry(en="No devices in use", key="x.empty"))
        self.assertPasses("R-EMPTY", entry(en="Buyer name missing", key="x.empty"))
        self.assertBlocks("R-EMPTY", entry(en="No records yet. Use “New entry” in the top right.", key="x.empty"))

    def test_business_words_not_dev_terms(self):
        self.assertPasses("R-DEV", entry(th="แคชเชียร์เช็ค", key="x.label"))
        self.assertPasses("R-DEV", entry(zh="未实现汇兑损益科目", key="x.label"))
        self.assertPasses("R-DEV", entry(zh="状态不能回退：{0} → {1}", key="x.error"))
        self.assertBlocks("R-DEV", entry(th="แคชหมดอายุ", key="x.error"))
        self.assertBlocks("R-DEV", entry(zh="该功能未实现", key="x.error"))
        self.assertBlocks("R-DEV", entry(zh="已回退到缓存", key="x.error"))

    def test_doctype_roles(self):
        data = ('{"doctype": "DocType", "permissions": [{"role": "System Manager", "read": 1}], "fields": ['
                '{"fieldname": "state", "fieldtype": "Select", "label": "Lease Expires At", "options": "\\nnot_billable\\nlegacy"}]}')
        found, admin = sources.doctype_info(data)
        self.assertTrue(admin)
        self.assertEqual(found["not_billable"], "option")
        self.assertEqual(found["Lease Expires At"], "label")
        self.assertFalse(sources.doctype_info(data.replace("System Manager", "All"))[1])
        option = entry(en="not_billable", zh="无需计费", keyed=False)
        option.role = rules.role_of(option, found, WORDS)
        self.assertEqual(option.role, "option")
        self.assertPasses("R-DEV", option)
        # 键列写的就是 msgid 本身的行也看 DocType
        same = entry(en="Lease Expires At", zh="租约到期时间", key="Lease Expires At", keyed=True)
        self.assertEqual(rules.role_of(same, found, WORDS), "label")
        # 只有 System Manager 能读的 DocType：后台，强术语只标黄；租户能读的（All）：产品界面，拦
        admin_label = entry(en="Lease Expires At", zh="租约到期时间", keyed=False, role="label")
        admin_label.extra["doctype_admin"] = True
        admin_label.surface = rules.surface_of(admin_label, "label")
        self.assertPasses("R-DEV", admin_label)
        self.assertBlocks("R-DEV", entry(en="Lease Expires At", zh="租约到期时间", keyed=False, role="label"))

    def test_paren_words(self):
        for text in ("净多缴税额（10 大于 8 时，或 9 加 10）", "上传总账（按会计期间汇总的明细）", "设置后立即生效（立即应用到本月）",
                     "账单中没有逐笔流水（仅有封面或汇总页）"):
            self.assertPasses("R-PAREN", entry(zh=text, key="x.error"))
        self.assertPasses("R-PAREN", entry(en="Statement period (May 2026 to June 2026)", key="x.label"))
        self.assertBlocks("R-PAREN", entry(zh="初始密码必填（用户拿它直接登录）", key="x.error"))
        self.assertBlocks("R-PAREN", entry(en="Upload the file (this may take a while)", key="x.error"))

    def test_talk_yao(self):
        for text in ("要更改存货科目组吗？", "要删除吗？", "要货申请", "要跟踪的单据类型"):
            self.assertPasses("R-TALK", entry(zh=text))
        self.assertBlocks("R-TALK", entry(zh="要人选"))

    def test_sentence_abbreviations(self):
        for text in ("Total incl. VAT", "Amount excl. VAT", "MR. ERP Co., Ltd. head office", "Invoice no. missing"):
            self.assertPasses("R-SENT", entry(en=text, key="x.label"))
        self.assertBlocks("R-SENT", entry(en="Saved. Pushed. Done.", key="x.label"))

    def test_emoji_status(self):
        table = entry(zh="⚠ 金额不一致", source="table", key="vat_check_i18n.py#_I18N.status")
        self.assertPasses("R-EMOJI", table)
        self.assertIn("R-EMOJI", hits(table, "warn"))
        self.assertPasses("R-EMOJI", entry(zh="✓ 已匹配", key="x.status"))
        self.assertBlocks("R-EMOJI", entry(zh="👋 欢迎回来", source="table", key="x#y"))
        self.assertBlocks("R-EMOJI", entry(zh="完成 🎉", key="x.status"))

    def test_name_allowed(self):
        allowed = entry(en="The push channel must be companion or cloud", zh="推送渠道须为 companion 或 cloud",
                        source="pearnly_core", keyed=False, key="The push channel must be companion or cloud")
        self.assertPasses("R-NAME", allowed)
        self.assertPasses("R-NAME", entry(zh="销售助手"))
        self.assertPasses("R-NAME", entry(th="ผู้ช่วยผู้จัดการฝ่ายขาย"))
        self.assertBlocks("R-NAME", entry(th="ติดตั้งผู้ช่วยก่อน"))

    def test_official_words(self):
        self.assertPasses("R-TALK", entry(zh="找不到文件夹", key="x.error"))
        self.assertIn("R-TALK", hits(entry(zh="找不到文件夹", key="x.error"), "warn"))
        self.assertPasses("R-WE", entry(zh="关于我们", en="About us", source="assistant", key="MESSAGES.about"))
        self.assertPasses("R-TALK", entry(en="just now", source="assistant", key="MESSAGES.justNow"))
        self.assertPasses("R-DEV", entry(en="Queued", source="assistant", key="MESSAGES.queued"))
        self.assertPasses("R-TALK", entry(en="Received amount (as actually credited by the bank)", source="pearnly_control", keyed=False))
        self.assertBlocks("R-TALK", entry(en="Actually, this is fine"))
        self.assertBlocks("R-WE", entry(en="We are syncing your files"))

    def test_literal_words(self):
        for text in ("逐一下载", "统一下发", "第 1～3 页", "营业时间 9:00～18:00"):
            self.assertPasses("R-TALK", entry(zh=text))
        self.assertPasses("R-TALK", entry(zh="下面的明细"))
        self.assertBlocks("R-TALK", entry(zh="请稍等一下"))
        self.assertBlocks("R-TALK", entry(zh="好的～"))

    def test_lease_json_call(self):
        self.assertPasses("R-DEV", entry(en="Lease", key="dms.card.TXT_PAY_LEASE", source="pearnly_dms"))
        self.assertPasses("R-DEV", entry(en="Export JSON", source="assistant", key="MESSAGES.exportJson"))
        self.assertPasses("R-DEV", entry(zh="调用工具", source="assistant", key="MESSAGES.toolCall"))
        self.assertBlocks("R-DEV", entry(en="Lease expires at {0}", key="x.label"))

    def test_guide_role(self):
        ios = entry(zh="相机权限已关闭。请到 iPhone「设置 → LINE」打开「相机」，返回 LINE 后会自动重试。",
                    key="common.scan.in_line_ios", source="pearnly_core")
        self.assertEqual(ios.role, "guide")
        self.assertPasses("R-NARR", ios)
        self.assertPasses("R-SENT", ios)
        self.assertBlocks("R-TALK", entry(zh="LINE 的相机权限被关闭了。请到「设置 → LINE」打开「相机」。",
                                          key="common.scan.in_line_ios", source="pearnly_core"))

    # ── 漏拦 ─────────────────────────────────────────────────────────────

    def test_english_dev_words_inside_chinese_and_thai(self):
        for item in (entry(zh="无法连接 Redis", source="assistant", key="MESSAGES.x"),
                     entry(zh="token 已过期", source="assistant", key="MESSAGES.x"),
                     entry(th="API ไม่ตอบสนอง", source="assistant", key="MESSAGES.x"),
                     entry(zh="请求 payload 过大", key="x.error")):
            self.assertBlocks("R-DEV", item)
        self.assertPasses("R-DEV", entry(zh="请在 Express 中打开", source="assistant", key="MESSAGES.x"))

    def test_chinese_word_gaps(self):
        bad = {
            "R-HEDGE": ["保存失败：服务器返回了错误，可能是网络不稳定，请稍后重试"],
            "R-WHY": ["为避免重复推送，同一单据只会推送一次"],
            "R-HINT": ["注：仅统计已推送的单据", "备注：金额按含税价计算", "建议先完成对账再关账"],
            "R-NARR": ["正在后台同步，完成后自动刷新", "保存后，系统会自动推送并通知相关人员"],
            "R-WE": ["已为您自动选择最近使用的账套", "已帮您跳过重复的单据"],
            "R-TALK": ["好的，马上为您处理", "稍等，正在思考…", "这些单据已推送", "这一步出错", "识别中途停止了，请再试一次",
                       "请换个仓库试试", "税报里一笔都没有"],
            "R-HELLO": ["抱歉，出了点问题", "不好意思，暂时无法保存", "此功能正在开发中，敬请期待"],
            "R-DEV": ["识别失败，已降级为普通模式", "此功能正在开发中"],
        }
        for rule, texts in bad.items():
            for text in texts:
                self.assertBlocks(rule, entry(zh=text, source="assistant", key="MESSAGES.x"))
        for text in ("建议零售价", "已推送", "保存失败，请重试", "以下单据已推送"):
            item = entry(zh=text, source="assistant", key="MESSAGES.x")
            self.assertFalse(hits(item), text)

    def test_english_word_gaps(self):
        bad = {
            "R-WHY": ["Due to a network error, the file was not saved"],
            "R-HEDGE": ["This may take a few minutes depending on the number of files.", "Couldn't reach the server — it might be down"],
            "R-HINT": ["Note that deleted records cannot be restored.", "Heads-up: drafts are not synced", "FYI: drafts are not synced"],
            "R-TALK": ["Looks like something went wrong.", "Upload your first document to get started", "Click here to learn more",
                       "You'll need to sign in again"],
            "R-HELLO": ["Sorry, something went wrong"],
        }
        for rule, texts in bad.items():
            for text in texts:
                self.assertBlocks(rule, entry(en=text))
        self.assertIn("R-NARR", hits(entry(en="Retrying in the background"), "warn"))
        for text in ("Not pushed yet", "Statement period (May 2026)", "Contact us"):
            self.assertFalse(hits(entry(en=text, key="x.label")), text)

    def test_thai_word_gaps(self):
        for text in ("เราไม่สามารถโหลดเอกสารได้", "ติดต่อเราได้ตลอด", "ผมจะช่วยคุณจัดการ"):
            self.assertBlocks("R-WE", entry(th=text))
        for text in ("คุณสามารถแก้ไขได้ภายหลัง", "อัปโหลดเอกสารแรกของคุณเลย", "รอแป๊บนึง"):
            self.assertBlocks("R-TALK", entry(th=text))
        self.assertBlocks("R-HELLO", entry(th="ขออภัย เกิดข้อผิดพลาด"))
        self.assertBlocks("R-DEV", entry(th="โทเค็นหมดอายุ", source="assistant", key="MESSAGES.x"))
        self.assertBlocks("R-NARR", entry(th="ระบบจะส่งให้อัตโนมัติ ไม่ต้องกดอะไร"))
        for text in ("สถานะ", "ข้อเสนอแนะ", "เกี่ยวกับเรา", "ติดต่อเรา", "เราเตอร์", "คำนึงถึงภาษี"):
            self.assertFalse({"R-WE", "R-TALK"} & hits(entry(th=text)), text)

    def test_tsx_bypasses(self):
        src = "\n".join([
            'export function A({ busy, n }) {',
            '  toast.show("Saved! We will push it automatically because the ledger is free");',
            '  return (<div>',
            '    <button title={`Retry ${n} times because the API timed out`} aria-label={busy ? "Hang on, we are loading" : "Done"} />',
            '    <p>{"Nothing here yet, we will take care of the rest"}</p>',
            '    <p>Are you sure? It is gone because we delete it</p>',
            '    <p>Upload (we will process it automatically)</p>',
            '    <span>Save & close</span>',
            '    <Tooltip content="The backend caches this" />',
            '    <Empty description="Just drop files here" />',
            '    <i aria-label={t(open ? "close" : "open")} title={tr("cowork.folder.day")} />',
            '    <Row label={<span className="text-danger text-xs">{tr("x.y")}</span>} />',
            '    <p>{t("send")}{t("stop")}</p>',
            '  </div>);',
            '}',
            'function pick<T>(table: Record<string, T>, key: unknown): T | undefined { return undefined; }',
        ])
        found = refs_of("frontend/assistant/src/a.tsx", src)
        texts = {text for _, text in found}
        for want in ("toast.show=Saved! We will push it automatically because the ledger is free",
                     "title=Retry {x} times because the API timed out", "aria-label=Hang on, we are loading", "aria-label=Done",
                     "Nothing here yet, we will take care of the rest", "Are you sure? It is gone because we delete it",
                     "Upload (we will process it automatically)", "Save & close", "content=The backend caches this",
                     "description=Just drop files here"):
            self.assertIn(want, texts)
        for unwanted in ("aria-label=close", "aria-label=open", "label=text-danger text-xs"):
            self.assertNotIn(unwanted, texts)
        self.assertFalse([t for t in texts if "Record" in t or "unknown" in t], texts)
        self.assertIn("tsx-concat", {kind for kind, _ in found})

    def test_desk_js_bypasses(self):
        src = "\n".join([
            'frappe.msgprint("We could not save because the API failed");',
            'frappe.show_alert({message: "Saved, we will sync it later", indicator: "green"});',
            'frappe.confirm("Really delete? We cannot undo this");',
            'frm.set_intro(`Heads up: ${n} rows are cached`);',
            'frm.dashboard.set_headline("Heads up, the backend is slow");',
            'frappe.msgprint(__("Failed") + ": " + "the endpoint returned a payload");',
            'const a = __(`Retry ${n} times`);',
            'const b = __("Saved") + " " + __("Done");',
            'frappe.msgprint(__("Saved"));',
            'const c = __(`cowork.policy.${x}`);',
            "const d = '<div class=\"text-muted\">' + __(\"Saved\") + '</div>';",
        ])
        found = refs_of("apps/pearnly_cowork/pearnly_cowork/public/desk/x.js", src)
        bare = {text for kind, text in found if kind == "desk-bare"}
        self.assertEqual(len(bare), 5, bare)
        kinds = [kind for kind, _ in found]
        self.assertIn("desk-dynamic", kinds)
        self.assertEqual(sum(1 for kind, text in found if kind == "desk-concat"), 2, found)
        self.assertNotIn(("desk-dynamic", "cowork.policy.{x}"), found)

    def test_python_bypasses(self):
        src = "\n".join([
            "import frappe",
            "from frappe import _",
            "def f(n, role, status):",
            "    frappe.throw(f'Could not save {n} because the cache expired')",
            "    frappe.throw(msg='We are retrying in the background')",
            "    frappe.msgprint(_('Saved') + ' — we will push it automatically')",
            "    frappe.throw(_(f'Retry {n} in the worker queue'))",
            "    frappe.throw('Oops')",
            "    frappe.publish_realtime('msgprint', 'The worker picked it up')",
            "    raise frappe.ValidationError('Not allowed here because of the lease')",
            "    x = _('Saved') + _('Pushed')",
            "    frappe.throw(_('Saved'))",
            "    y = _(f'cowork.work.status.{status}')",
            "    frappe.throw('dms_booking.not_bound')",
        ])
        found = refs_of("apps/pearnly_cowork/pearnly_cowork/x.py", src)
        bare = {text for kind, text in found if kind == "py-bare"}
        self.assertEqual(bare, {"Could not save {x} because the cache expired", "We are retrying in the background", "Oops",
                                "The worker picked it up", "Not allowed here because of the lease"})
        kinds = [kind for kind, _ in found]
        self.assertEqual(kinds.count("py-concat"), 2)
        self.assertEqual(kinds.count("py-dynamic"), 1)

    def test_html_and_companion(self):
        html = "<p>Welcome! We will set everything up for you.</p><a>hello@pearnly.com</a><input placeholder=\"Your email here\">"
        found = refs_of("apps/pearnly_control/pearnly_control/www/x.html", html)
        self.assertIn(("html-hard", "Welcome! We will set everything up for you."), found)
        self.assertIn(("html-hard", "placeholder=Your email here"), found)
        self.assertNotIn("hello@pearnly.com", {text for _, text in found})
        probe = "\n".join([
            "from tkinter import messagebox",
            "from .i18n import t",
            "def show(log):",
            "    messagebox.showinfo('小助手', '我们会在后台自动同步，因为接口缓存过期了')",
            "    QLabel('Companion is syncing your files')",
            "    log.warning('dbf task retry later: %s')",
            "    t('no_such_key')",
            "    return {'message': 'Companion is syncing, the API cache expired'}",
        ])
        found = refs_of("companion/src/companion/probe_gui.py", probe)
        hard = {text for kind, text in found if kind == "cc-desktop-hard"}
        self.assertEqual(hard, {"小助手", "我们会在后台自动同步，因为接口缓存过期了", "Companion is syncing your files",
                                "Companion is syncing, the API cache expired"})
        self.assertIn(("cc-desktop-key", "no_such_key"), found)
        self.assertFalse(sources.code_refs("companion/src/companion/i18n.py", probe))

    def test_doctype_json_texts(self):
        data = ('{"doctype": "DocType", "fields": [{"fieldname": "a", "fieldtype": "Data", "label": "Balance",'
                ' "description": "We sync this from the backend API every 5 minutes because the cache expires"},'
                ' {"fieldname": "s", "fieldtype": "Select", "options": "cowork\\nerp"}]}')
        found = refs_of("apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/x/x.json", data)
        self.assertIn(("doctype-text", "We sync this from the backend API every 5 minutes because the cache expires"), found)
        self.assertNotIn(("doctype-text", "cowork"), found)  # Select 取值码不要求进字典
        snap = sources.Snapshot({}, {"Balance"}, {}, {}, set(), {}, {}, {})
        levels = {text: rules.check_ref(sources.CodeRef(kind, text, "x", 1), snap, None, UPSTREAM) for kind, text in found}
        self.assertFalse(levels["Balance"])
        self.assertTrue(levels["We sync this from the backend API every 5 minutes because the cache expires"])

    def test_unknown_role_is_not_looser(self):
        self.assertBlocks("R-LEN", entry(zh="这是一段没有角色词的键名对应的很长很长很长很长很长很长很长很长很长很长很长的说明文字内容", key="x.y"))
        self.assertEqual(rules.ZH_LIMITS["unknown"], rules.ZH_LIMITS["hint"])


TSV_HEAD = "en\tzh\tth\tja\tkey\n"
I18N_TS_ONE_LINE = (
    "const MESSAGES = {\n"
    "  en: { send: \"Send\", stop: \"Stop\" },\n"
    "  zh: { send: \"发送\", stop: \"停止\" },\n"
    "};\n"
)
I18N_TS_MULTI = (
    "const MESSAGES = {\n"
    "  en: {\n    send: \"Send\",\n    stop: \"Stop\",\n  },\n"
    "  zh: {\n    send: \"发送\",\n    stop: \"停止\",\n  },\n"
    "};\n"
)


class TempRepo(unittest.TestCase):
    """临时 git 仓库里跑真实的分类与判定。"""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="copy-gate-test-")
        self.git("init", "-q", "-b", "main")
        self.write("apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv", TSV_HEAD + "".join([
            "Push\t推送\tส่ง\tプッシュ\tcowork.push.btn\n",
            "No push target\t未选择推送目标\tยังไม่ได้เลือกปลายทาง\t送信先未選択\tcowork.company.pending.no_target\n",
            "Could not read the file\t无法读取文件\tอ่านไฟล์ไม่ได้\tファイルを読み取れません\t\n",
            "Old name\t旧名称\tชื่อเก่า\t旧名\tcowork.name.old\n",
        ]))
        self.write("apps/pearnly_cowork/pearnly_cowork/translations/en.csv", "cowork.push.btn,Push\n")
        self.write("frontend/assistant/src/i18n.ts", I18N_TS_ONE_LINE)
        self.write("frontend/assistant/src/a.tsx", "export const A = () => <div>{t(\"send\")}</div>;\n")
        self.base = self.commit("base")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", self.dir, "-c", "core.hooksPath=/dev/null", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def write(self, rel, text):
        path = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)

    def edit(self, rel, old, new):
        path = os.path.join(self.dir, rel)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        self.write(rel, text.replace(old, new))

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def run_gate(self, pol=None):
        base_tree, cur_tree = repo.GitTree(self.dir, self.base), repo.WorkTree(self.dir)
        base, cur = sources.snapshot(base_tree), sources.snapshot(cur_tree)
        for snap in (base, cur):
            check_copy._assign_roles(snap)
        changes = diff.classify(base, cur)
        pol = pol or policy.Policy([], [], "exempt", "approved")
        findings = check_copy.check_changes(changes, CHECKER, pol)
        refs = diff.new_code_refs(base_tree, cur_tree, diff.changed_code_paths(base_tree, cur_tree))
        refs += diff.manual_translation_rows(base, cur)
        known = set().union(*cur.glossary_msgids.values(), *base.translations.values())
        findings += check_copy.check_refs(refs, cur, pol, known)
        findings += pol.validate(cur, cur_tree, "exempt")
        kinds = {c.ident: c.kind for c in changes}
        return kinds, findings

    @staticmethod
    def blocked(findings):
        return {(f.rule, f.entry_id) for f in findings if f.level == "block"}

    def test_new_entries(self):
        tsv = "apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv"
        self.edit(tsv, "Old name\t", "Not pushed yet\t还没推送\tยังไม่ได้ส่ง\tまだ送信していません\tcowork.ledger.not_pushed\nOld name\t")
        self.edit(tsv, "Old name\t", "Not read back\t未回读\tยังไม่ได้อ่านกลับ\t未読み戻し\tcowork.ledger.readback\nOld name\t")
        kinds, findings = self.run_gate()
        self.assertEqual(kinds["pearnly_cowork:cowork.ledger.not_pushed"], "new")
        self.assertIn(("R-TALK", "pearnly_cowork:cowork.ledger.not_pushed"), self.blocked(findings))
        self.assertNotIn("pearnly_cowork:cowork.ledger.readback", {e for _, e in self.blocked(findings)})
        # 整个入口：有拦截退出码 1
        self.assertEqual(quiet_main(["--repo", self.dir, "--base", self.base]), 1)

    def test_changed_needs_approval(self):
        tsv = "apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv"
        self.edit(tsv, "\t未选择推送目标\t", "\t未选推送目标\t")
        kinds, findings = self.run_gate()
        ident = "pearnly_cowork:cowork.company.pending.no_target"
        self.assertEqual(kinds[ident], "changed")
        self.assertIn(("R-CHG", ident), self.blocked(findings))
        approved = policy.Policy([], [(1, [ident, "zh", "未选推送目标", "2026-10-17", "主对话 2026-10-17 开发窗口"])], "e", "a")
        _, findings = self.run_gate(approved)
        self.assertNotIn(("R-CHG", ident), self.blocked(findings))
        # 登记的文案和当前不一致：照样拦
        stale = policy.Policy([], [(1, [ident, "zh", "未选择目标", "2026-10-17", "主对话 2026-10-17 开发窗口"])], "e", "a")
        _, findings = self.run_gate(stale)
        self.assertIn(("R-CHG", ident), self.blocked(findings))

    def test_msgid_rewrite_is_translation_change(self):
        tsv = "apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv"
        self.edit(tsv, "Could not read the file\t", "Could not read this file\t")
        kinds, findings = self.run_gate()
        self.assertEqual(kinds.get("pearnly_cowork:Could not read this file"), "translation")
        self.assertFalse([f for f in findings if f.rule == "R-CHG"])

    def test_key_rename_is_move(self):
        tsv = "apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv"
        self.edit(tsv, "\tcowork.name.old\n", "\tcowork.name.previous\n")
        kinds, findings = self.run_gate()
        self.assertEqual(kinds["pearnly_cowork:cowork.name.previous"], "moved")
        self.assertFalse(self.blocked(findings))

    def test_i18n_ts_reflow_is_not_a_change(self):
        self.write("frontend/assistant/src/i18n.ts", I18N_TS_MULTI)
        kinds, findings = self.run_gate()
        self.assertEqual(kinds, {})
        self.assertFalse(self.blocked(findings))
        self.edit("frontend/assistant/src/i18n.ts", "send: \"发送\"", "send: \"发一下\"")
        kinds, findings = self.run_gate()
        self.assertEqual(kinds["assistant:MESSAGES.send"], "changed")
        self.assertIn(("R-TALK", "assistant:MESSAGES.send"), self.blocked(findings))

    def test_exemption_binds_text(self):
        tsv = "apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv"
        self.edit(tsv, "Old name\t", "Pick one\t要人选\tต้องเลือก\t要選択\tcowork.ledger.choice\nOld name\t")
        ident = "pearnly_cowork:cowork.ledger.choice"
        exempt = policy.Policy([(1, [ident, "R-TALK", "要人选", "测试用：豁免绑定当时的文案"])], [], "e", "a")
        _, findings = self.run_gate(exempt)
        self.assertNotIn(("R-TALK", ident), self.blocked(findings))
        moved = policy.Policy([(1, [ident, "R-TALK", "要人来选", "测试用：豁免绑定当时的文案"])], [], "e", "a")
        _, findings = self.run_gate(moved)
        self.assertIn(("R-TALK", ident), self.blocked(findings))
        self.assertTrue([f for f in findings if f.rule == "R-EXEMPT" and "失效" in f.message])

    def test_exemption_format(self):
        rows = [
            (1, ["pearnly_cowork:*", "R-TALK", "x", "通配豁免全部词条"]),
            (2, ["pearnly_cowork:a", "*", "x", "全部规则都豁免掉"]),
            (3, ["pearnly_cowork:a", "R-TALK", "x", "历史遗留"]),
            (4, ["术语:Token", "R-TALK", "Token", "术语只能豁免开发词"]),
            (5, ["pearnly_cowork:a", "R-CHG", "x", "改文案不能靠豁免过闸"]),
        ]
        pol = policy.Policy(rows, [(9, ["a:b", "zh", "x", "10/17", "?"])], "e", "a")
        self.assertEqual(len(pol.problems), 6)
        self.assertFalse(pol.exemptions)

    def test_hardcoded_jsx_and_translation_rows(self):
        self.write("frontend/assistant/src/a.tsx",
                   "export const A = () => <div>{t(\"send\")}<span>Toggle Sidebar</span></div>;\n")
        self.write("apps/pearnly_cowork/pearnly_cowork/translations/en.csv", "cowork.push.btn,Push\nHand made,Hand made\n")
        self.write("apps/pearnly_cowork/pearnly_cowork/push.py",
                   "from frappe import _\n\ndef f():\n    return _(\"Hand made\"), _(\"Push\")\n")
        _, findings = self.run_gate()
        found = {(f.rule, f.level, f.snippet) for f in findings}
        self.assertIn(("R-HARD", "block", "Toggle Sidebar"), found)
        self.assertIn(("R-HARD", "warn", "Hand made"), found)  # 翻译包新增行：标黄
        # 新代码用了只在本次翻译包新增行里的词：不在术语表 → 短词标黄，句子会拦
        self.assertTrue([f for f in findings if f.rule == "R-HARD" and "_()" in f.message])

    def snapshot_dir(self):
        """像提交钩子那样把暂存区导出到临时目录。"""
        out = tempfile.mkdtemp(prefix="copy-gate-staged-")
        self.addCleanup(shutil.rmtree, out, True)
        self.git("checkout-index", "--all", "--prefix=%s/" % out)
        return out

    def test_rename_and_rewrite_in_i18n_ts_is_a_change(self):
        """换键名同时改字（somethingWrong → genericFailure）照样算改已有文案，要登记。"""
        self.write("frontend/assistant/src/i18n.ts", I18N_TS_MULTI)
        self.commit("multi-line")
        self.base = self.git("rev-parse", "HEAD")
        self.edit("frontend/assistant/src/i18n.ts", 'stop: "Stop"', 'halt: "Halt now"')
        self.edit("frontend/assistant/src/i18n.ts", 'stop: "停止"', 'halt: "立即中止"')
        kinds, findings = self.run_gate()
        self.assertEqual(kinds.get("assistant:MESSAGES.halt"), "changed")
        self.assertIn(("R-CHG", "assistant:MESSAGES.halt"), self.blocked(findings))

    def test_approval_needs_work_record(self):
        """登记的出处必须是 docs/work/*.md，且那份记录逐字有新文案；「主对话确认过」一句话不算。"""
        ident = "pearnly_cowork:cowork.company.pending.no_target"
        docs = {"docs/work/2026-10-17-x.md": "| cowork·cowork.company.pending.no_target | 未选择推送目标 → 未选推送目标 |"}
        good = policy.Policy([], [(1, [ident, "zh", "未选推送目标", "2026-10-17", "docs/work/2026-10-17-x.md 第 1 节"])], "e", "a")
        good.verify_sources(docs.get, "a")
        self.assertTrue(good.approved(ident, "zh", "未选推送目标"))
        self.assertFalse(good.problems)
        for where in ("主对话 2026-10-17 开发窗口", "docs/work/missing.md", "docs/work/2026-10-17-x.md"):
            text = "别的文案" if where.endswith("x.md") else "未选推送目标"
            bad = policy.Policy([], [(1, [ident, "zh", text, "2026-10-17", where])], "e", "a")
            bad.verify_sources(docs.get, "a")
            self.assertFalse(bad.approvals, where)
            self.assertTrue([f for f in bad.problems if f.rule == "R-EXEMPT"], where)
        # --approve 不写 docs/work 记录就拒绝
        self.assertEqual(quiet_main(["--repo", self.dir, "--base", self.base, "--approve", "--source", "主对话已确认"]), 2)

    def test_term_rows_need_source_and_full_name(self):
        dev = check_copy._dev_patterns(WORDS)
        rows = [
            (1, ["术语:接口@pearnly_cowork", "R-DEV", "接口", "本产品中的接口指外部连接的业务名称"]),
            (2, ["术语:Channel Access Token", "R-DEV", "Channel Access Token", "LINE 控制台栏名，照原名"]),
            (3, ["术语:Channel Access Token@pearnly_control", "R-DEV", "Channel Access Token", "LINE 控制台栏名，照原名"]),
        ]
        pol = policy.Policy(rows, [], "e", "a", dev)
        self.assertEqual(len(pol.problems), 2)
        self.assertEqual([e.scope for e in pol.exemptions], ["术语:Channel Access Token@pearnly_control"])

    def _args(self, **extra):
        values = dict(head=None, staged=False, base=self.base, approve=False, report=False, out=None, tree=None, source=None)
        values.update(extra)
        return argparse.Namespace(**values)

    def _run_diff(self, **extra):
        cur_tree = repo.WorkTree(self.dir)
        cur = sources.snapshot(cur_tree)
        check_copy._assign_roles(cur, WORDS)
        pol = policy.Policy([], [], "scripts/i18n/copy-exempt.tsv", "scripts/i18n/copy-approved.tsv")
        with contextlib.redirect_stdout(io.StringIO()):
            return check_copy.run_diff(self._args(**extra), self.dir, cur_tree, cur, CHECKER, pol, UPSTREAM)

    def test_loosening_rules_with_copy_changes_blocks(self):
        """同一差异里放宽词表又加文案：拦（词表改动单独提交、单独过 CI）。"""
        self.write("scripts/i18n/copy-exempt.tsv", "# 豁免\n")
        self.write("scripts/i18n/copy-words.tsv", "R-TALK\tzh\tblock\t还没\t进度口语\nR-TALK\tzh\tblock\t这个\t指代\n")
        self.base = self.commit("tables")
        self.write("scripts/i18n/copy-words.tsv", "R-TALK\tzh\tblock\t这个\t指代\n")
        self.assertEqual(self._run_diff(), 0)  # 只改词表：放行（清单里列出规则改动）
        self.edit("apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv", "Old name\t",
                  "Read back\t已回读\tอ่านกลับแล้ว\t読み戻し済み\tcowork.ledger.readback_done\nOld name\t")
        self.assertEqual(self._run_diff(), 1)

    def test_staged_mode_only_checks_this_commit(self):
        """提交钩子：分支早先提交的问题不拦与文案无关的提交；本次提交新加的才拦。"""
        tsv = "apps/pearnly_cowork/pearnly_cowork/i18n/messages.tsv"
        self.edit(tsv, "Old name\t", "Not pushed\t还没推送\tยังไม่ได้ส่ง\t未送信\tcowork.ledger.not_pushed\nOld name\t")
        self.commit("earlier commit with colloquial copy")
        self.write("frontend/assistant/src/a.tsx", "export const A = () => <div>{t(\"stop\")}</div>;\n")
        self.git("add", "-A")
        self.assertEqual(quiet_main(["--repo", self.dir, "--staged", "--tree", self.snapshot_dir()]), 0)
        self.assertEqual(quiet_main(["--repo", self.dir, "--base", self.base]), 1)  # 分支累计：拦
        self.edit(tsv, "Old name\t", "Pick one\t要人选\tต้องเลือก\t要選択\tcowork.ledger.choice\nOld name\t")
        self.git("add", "-A")
        self.assertEqual(quiet_main(["--repo", self.dir, "--staged", "--tree", self.snapshot_dir()]), 1)

    def test_doctype_and_companion_code_in_diff(self):
        self.write("apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/wallet/wallet.json",
                   '{"doctype": "DocType", "fields": [{"fieldname": "a", "fieldtype": "Data", "label": "Push"}]}\n')
        self.write("companion/src/companion/gui.py", "def show():\n    return None\n")
        self.base = self.commit("doctype")
        self.write("apps/pearnly_cowork/pearnly_cowork/pearnly_cowork/doctype/wallet/wallet.json",
                   '{"doctype": "DocType", "fields": [{"fieldname": "a", "fieldtype": "Data", "label": "Push",'
                   ' "description": "We sync this from the backend API every 5 minutes"}]}\n')
        self.write("companion/src/companion/gui.py", "def show(box):\n    box.setText('We are syncing your files')\n")
        _, findings = self.run_gate()
        snippets = {f.snippet for f in findings if f.rule == "R-HARD" and f.level == "block"}
        self.assertIn("We sync this from the backend API every 5 minutes", snippets)
        self.assertIn("We are syncing your files", snippets)

    def test_worktree_ignores_build_output(self):
        self.write(".gitignore", "apps/pearnly_core/pearnly_core/public/assistant/\n")
        self.write("apps/pearnly_core/pearnly_core/public/assistant/assets/index.js", "frappe.msgprint('Built output text here');\n")
        self.write("apps/pearnly_core/pearnly_core/public/desk/real.js", "frappe.msgprint('Hand written text here');\n")
        paths = repo.WorkTree(self.dir).paths()
        self.assertIn("apps/pearnly_core/pearnly_core/public/desk/real.js", paths)
        self.assertNotIn("apps/pearnly_core/pearnly_core/public/assistant/assets/index.js", paths)

    def test_shallow_clone_hint(self):
        clone = tempfile.mkdtemp(prefix="copy-gate-shallow-")
        self.addCleanup(shutil.rmtree, clone, True)
        subprocess.run(["git", "clone", "-q", "--depth", "1", "file://" + self.dir, clone], check=True, capture_output=True)
        self.git("commit", "-q", "--allow-empty", "-m", "main moved on")
        # 照着旧提示 git fetch --depth 1 origin main：origin/main 前进了，但历史还是截断的，merge-base 取不到
        subprocess.run(["git", "-C", clone, "fetch", "-q", "--depth", "1", "origin"],
                       check=True, capture_output=True)
        with self.assertRaises(diff.BaseError) as caught:
            diff.resolve_base(clone, "auto")
        self.assertIn("fetch-depth: 0", str(caught.exception))

    def test_base_resolution(self):
        os.environ["COPY_GATE_BASE"] = "0" * 40
        try:
            with self.assertRaises(diff.BaseError):  # 全 0 忽略；临时仓库没有 origin/main
                diff.resolve_base(self.dir, "auto")
        finally:
            del os.environ["COPY_GATE_BASE"]
        self.assertEqual(diff.resolve_base(self.dir, self.base), self.base)
        self.assertEqual(quiet_main(["--repo", self.dir, "--base", "no-such-rev"]), 2)
        self.assertEqual(quiet_main(["--repo", self.dir, "--base", self.base]), 0)


if __name__ == "__main__":
    unittest.main()
