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


def quiet_main(argv):
    """跑 check-copy 的入口，吞掉它的终端输出。"""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return check_copy.main(argv)


def entry(zh=None, en=None, th=None, ja=None, key="cowork.sample.text", source="pearnly_cowork", role=None,
          keyed=True, surface=None):
    texts = {k: v for k, v in (("zh", zh), ("en", en), ("th", th), ("ja", ja)) if v}
    item = sources.Entry(source, key, texts)
    item.extra["keyed"] = keyed
    item.role = role or rules.role_of(item, {})
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
        multi = entry(zh="DMS 连接中断，本次读取已暂停。系统将从断点继续读取，已完成的组仍保留。", key="dms.err.ERR_DMS_SNAPSHOT_RETRY",
                      source="pearnly_dms")
        self.assertBlocks("R-NARR", multi)
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
        self.assertBlocks("R-HELLO", entry(zh="完成 🎉"))
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
            found = rules.check_ref(sources.CodeRef(kind, text, "x", 1), snap)
            return found[0].level if found else None

        self.assertEqual(level("tsx-text", "Toggle Sidebar"), "block")
        self.assertEqual(level("tsx-attr", "aria-label=Toggle Sidebar"), "block")
        self.assertIsNone(level("tsx-text", "CC"))
        self.assertEqual(level("py-msgid", "Nothing was really connected yet"), "block")
        self.assertEqual(level("py-msgid", "Not permitted"), "warn")  # Frappe 自带词
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
