"""表单排版闸门（scripts/check-form-layout.py）：认得出分栏写法、栏长差、成对字段、栏数与新建 / 只读两种场景，基线只许减少。"""
import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_form_layout", ROOT / "scripts/check-form-layout.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def problems(source):
    folder = tempfile.TemporaryDirectory()
    root = pathlib.Path(folder.name)
    path = root / "apps/a/a/public/desk/page.js"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    with mock.patch.object(check, "ROOT", root):
        check.helpers([path])
        forms = check.js_forms(path)
    folder.cleanup()
    return forms, sorted(k.split("::", 2)[2] for form in forms for k, _ in check.form_problems(form))


class FormLayout(unittest.TestCase):
    def test_pairs_each_in_own_section_pass(self):
        forms, found = problems("""
            p.dialog(__("New"), [
                {fieldtype: "Data", fieldname: "first_name", label: "First"},
                {fieldtype: "Column Break"},
                {fieldtype: "Data", fieldname: "last_name"},
                {fieldtype: "Section Break", hide_border: 1},
                {fieldtype: "Data", fieldname: "username"},
                {fieldtype: "Column Break"},
                {fieldtype: "Password", fieldname: "password"},
            ]);""")
        self.assertEqual(len(forms), 1)
        self.assertEqual(found, [])

    def test_side_by_side_pair_split_on_phone(self):
        # 电脑宽名 | 姓同一行，但左栏名下面还有用户名：手机上叠成单列是名、用户名、姓
        _, found = problems("""
            p.dialog(__("New"), [
                {fieldtype: "Data", fieldname: "first_name"},
                {fieldtype: "Data", fieldname: "username"},
                {fieldtype: "Column Break"},
                {fieldtype: "Data", fieldname: "last_name"},
                {fieldtype: "Password", fieldname: "password"},
            ]);""")
        self.assertEqual(found, ["pair:first_name|last_name::0", "pair:username|password::0"])

    def test_unbalanced_and_split_pair(self):
        _, found = problems("""
            const f = (name, label, type, extra) => ({fieldname: name, label, fieldtype: type, ...extra});
            p.layout(page.main, [
                f("first_name", "First Name", "Data"),
                f("username", "Username", "Data"),
                f("last_name", "Last Name", "Data"),
                f("email", "Email", "Data"),
                f("note", "Note", "Small Text"),
                {fieldtype: "Column Break"},
                f("password", "Password", "Password"),
            ]);""")
        # 名 | 姓中间夹了用户名；用户名在左栏第二行、密码在右栏第一行
        self.assertEqual(found, ["balance::0", "pair:first_name|last_name::0", "pair:username|password::0"])

    def test_pair_stacked_in_one_column_passes(self):
        _, found = problems("""
            p.layout(page.main, [
                {fieldtype: "Data", fieldname: "email"},
                {fieldtype: "Data", fieldname: "first_name"},
                {fieldtype: "Data", fieldname: "last_name"},
                {fieldtype: "Column Break"},
                {fieldtype: "Data", fieldname: "username"},
                {fieldtype: "Data", fieldname: "company"},
                {fieldtype: "Data", fieldname: "domain"},
            ]);""")
        self.assertEqual(found, [])

    def test_too_many_columns(self):
        _, found = problems("""
            p.layout(page.main, [
                {fieldtype: "Data", fieldname: "a"}, {fieldtype: "Column Break"},
                {fieldtype: "Data", fieldname: "b"}, {fieldtype: "Column Break"},
                {fieldtype: "Data", fieldname: "c"}, {fieldtype: "Column Break"},
                {fieldtype: "Data", fieldname: "d"},
            ]);""")
        self.assertEqual(found, ["columns::0"])

    def test_helpers_hidden_and_scenarios(self):
        # u.column() 认作分栏；hidden: 1 不算；hidden: editable / !editable 分两种场景各算一遍
        forms, found = problems("""
            u.column = () => ({fieldtype: "Column Break"});
            u.fields = (state) => { const {editable} = state, f = u.field, col = u.column; return [
                f("doc_date", "Date", "Date"), f("ghost", "Ghost", "Data", {hidden: 1}), col(),
                f("is_paid", "Is Paid", "Check", {hidden: !editable}),
                f("paid_amount", "Paid", "Currency", {hidden: editable}),
                f("payment_note", "Note", "Small Text", {hidden: editable}),
                f("payment_status", "Status", "Select", {hidden: editable}),
            ]; };""")
        self.assertEqual(set(forms[0]["scenarios"]), {"editable=1", "editable=0"})
        names = [[i.fieldname for i in column] for column in forms[0]["scenarios"]["editable=1"][0]]
        self.assertEqual(names, [["doc_date"], ["is_paid"]])
        self.assertEqual(found, ["balance::0@editable=0"])

    def test_conditional_spread_follows_flag(self):
        forms, _ = problems("""
            p.dialog(t, [
                {fieldtype: "Data", fieldname: "first_name"},
                {fieldtype: "Column Break"},
                {fieldtype: "Data", fieldname: "last_name"},
                ...(is_new ? [{fieldtype: "Password", fieldname: "password"}] : []),
            ]);""")
        right = {s: [i.fieldname for i in sec[0][1]] for s, sec in forms[0]["scenarios"].items()}
        self.assertEqual(right, {"is_new=1": ["last_name", "password"], "is_new=0": ["last_name"]})

    def test_strings_comments_and_regex_do_not_confuse_brackets(self):
        _, found = problems("""
            const re = /[\\]]+/g; // ] [ in a comment
            const s = "Column Break ] [";
            p.layout(page.main, [
                {fieldtype: "Data", fieldname: "a", label: `x ${y[0]} ]`},
                {fieldtype: "Column Break"},
                {fieldtype: "Data", fieldname: "b"},
            ]);""")
        self.assertEqual(found, [])

    def test_baseline_only_shrinks(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        baseline = pathlib.Path(folder.name) / "baseline.json"
        baseline.write_text(json.dumps({"gone::x::balance::0": "原因"}), encoding="utf-8")
        with mock.patch.object(check, "BASELINE", baseline), mock.patch.object(check, "all_forms", lambda: iter([])), \
                mock.patch("sys.argv", ["check", "--update"]):
            self.assertEqual(check.main(), 0)
        self.assertEqual(json.loads(baseline.read_text(encoding="utf-8")), {})


if __name__ == "__main__":
    unittest.main()
