#!/usr/bin/env python3
"""表单排版闸门：新建 / 编辑表单分栏要齐（用户 10-31：「横竖七八」）。

起因：加新字段时直接塞进当前那一栏、不重排——个人信息页「姓」落到「访问域名」下面、添加用户名姓分在两头、
新建公司拉成五栏、新建物料两栏一长一短。规矩见 docs/development/interface-copy.md 旁的
docs/work/2026-11-01-form-layout-audit.md：每栏放一类、各栏长度接近、成对字段同一行、同一份资料新建与详情同一套分栏。

查哪些表单：
- 前端（apps/**/public/**/*.js）：字段数组（`p.layout` / `p.dialog` / `frappe.ui.Dialog` / `FieldGroup` 用的
  `[{fieldtype: …}, …]`，含 `f("名", "标签", "Data")` 这类小工具函数写法）里带分栏的；
- DocType（apps/**/doctype/*/*.json，不含子表）：按字段顺序的 Section / Column Break。

报哪几类（按 Section 算；隐藏字段不算；行高按字段类型估：多行文本、表格算多行，带说明的加一点，带 depends_on 的算半格）：
- `columns`：一个 Section 超过三栏（电脑宽每栏太窄、手机上又拉得很长）；
- `balance`：两栏以上、最长一栏比最短一栏多出 LIMIT 行以上；
- `pair`：成对字段（名 | 姓、用户名 | 密码、起 | 止）既不是同一栏上下挨着，也不是「左栏最后一格 | 右栏第一格」并排
  （并排的一对要单独成一节，手机上叠成单列后才挨着）。

存量登记在 scripts/form-layout-baseline.json（每条写原因）：**只许减少**；修掉一条后用 `--update` 删掉它。
条件展开（`...(新建 ? [...] : [])`）按「展开」算，因为新建那一屏最满。

    python3 scripts/check-form-layout.py            # 检查；出现基线以外的问题就失败
    python3 scripts/check-form-layout.py --list     # 列出全部带分栏的表单与每栏的字段（审查清单用）
    python3 scripts/check-form-layout.py --update   # 按当前代码删掉已修好的基线条目（不许新增）

只读（--update 只写基线文件）；不连网络。
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "form-layout-baseline.json"
SKIP_PARTS = {"node_modules", "vendor", "dist", "pearnly-app", "assistant", "__pycache__"}
LIMIT = 1.5          # 栏长差（估算行数）超过这么多就报：差两行（一栏三格、一栏一格）已经一眼看得出一长一短
MAX_COLUMNS = 3
PAIRS = [("first_name", "last_name"), ("username", "password"), ("from_date", "to_date"),
         ("date_from", "date_to"), ("start_date", "end_date"), ("valid_from", "valid_upto")]
FIELDTYPES = {
    "Data", "Link", "Dynamic Link", "Select", "Autocomplete", "Int", "Float", "Currency", "Percent", "Date",
    "Datetime", "Time", "Duration", "Password", "Phone", "Read Only", "Rating", "Color", "Attach", "Attach Image",
    "Barcode", "Geolocation", "Icon", "Signature", "Button", "Check", "Heading", "Small Text", "Text", "Long Text",
    "Code", "Text Editor", "Markdown Editor", "HTML Editor", "JSON", "HTML", "Image", "Table", "Table MultiSelect",
    "MultiSelect", "MultiSelectPills", "MultiCheck", "Section Break", "Column Break", "Tab Break", "Fold",
}
HEIGHT = {"Check": 0.6, "Heading": 0.8, "Small Text": 2, "Text": 3, "Long Text": 3, "Code": 3, "Text Editor": 4,
          "Markdown Editor": 4, "HTML Editor": 4, "JSON": 3, "Table": 3, "Table MultiSelect": 1.5,
          "MultiSelectPills": 1.5, "MultiCheck": 2, "Signature": 2, "Geolocation": 4, "Image": 2,
          "HTML": 0}  # HTML 多是提示条 / 预览的空容器，看不出多高：不计，实测巡检里看
STRUCTURE = {"Section Break", "Column Break", "Tab Break", "Fold"}


# ── 一个够用的 JS 词法：字符串 / 模板 / 注释 / 正则跳过，括号配成树 ─────────────────────────────────
TOKEN = re.compile(r"\s+|//[^\n]*|/\*.*?\*/|[A-Za-z_$][\w$]*|\d[\w.]*|\.\.\.|=>|[^\s]", re.S)
REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^") | {"return", "typeof", "case", "=>", "in", "of", "else"}


def tokens(text: str):
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            out.append(("str", text[i + 1:j], i))
            i = j + 1
            continue
        if c == "`":
            j, depth = i + 1, 0
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if depth == 0 and text[j] == "`":
                    break
                if text.startswith("${", j):
                    depth += 1
                    j += 2
                    continue
                if depth and text[j] == "}":
                    depth -= 1
                j += 1
            out.append(("str", text[i + 1:j], i))
            i = j + 1
            continue
        if c == "/" and not text.startswith(("//", "/*"), i):
            prev = out[-1][1] if out else "("
            if out and out[-1][0] == "str":
                prev = "x"
            if prev in REGEX_BEFORE:
                j, klass = i + 1, False
                while j < n and (klass or text[j] != "/") and text[j] != "\n":
                    if text[j] == "\\":
                        j += 1
                    elif text[j] == "[":
                        klass = True
                    elif text[j] == "]":
                        klass = False
                    j += 1
                j += 1
                while j < n and text[j].isalpha():
                    j += 1
                out.append(("re", text[i:j], i))
                i = j
                continue
        m = TOKEN.match(text, i)
        piece = m.group(0)
        if not piece.isspace() and not piece.startswith(("//", "/*")):
            out.append(("id" if (piece[0].isalpha() or piece[0] in "_$") else "p", piece, i))
        i = m.end()
    return out


PAIR_OF = {"(": ")", "[": "]", "{": "}"}


def tree(toks):
    """把词法结果配成嵌套组：('grp', 开括号, [子项], 位置)。"""
    stack, root = [], []
    cur = root
    for tok in toks:
        if tok[0] == "p" and tok[1] in PAIR_OF:
            group = ("grp", tok[1], [], tok[2])
            cur.append(group)
            stack.append(cur)
            cur = group[2]
        elif tok[0] == "p" and tok[1] in ")]}":
            if stack:
                cur = stack.pop()
        else:
            cur.append(tok)
    return root


def split_commas(items):
    parts, cur = [], []
    for item in items:
        if item[0] == "p" and item[1] == ",":
            parts.append(cur)
            cur = []
        else:
            cur.append(item)
    if cur:
        parts.append(cur)
    return parts


def obj_props(group):
    """对象字面量顶层的 键: 值（值只留第一个词）。"""
    props = {}
    for part in split_commas(group[2]):
        if len(part) >= 3 and part[0][0] in ("id", "str") and part[1][1] == ":":
            value = part[2]
            props[part[0][1]] = (value[1] if value[0] in ("str", "id") or value[0] == "p" else value, part[2:])
        elif len(part) == 1 and part[0][0] == "id":
            props[part[0][1]] = (part[0][1], part)  # 简写 {fieldname}
    return props


class Field:
    def __init__(self, fieldtype, fieldname="", described=False, line=0, conditional=False):
        self.fieldtype, self.fieldname, self.described, self.line = fieldtype, fieldname, described, line
        self.conditional = conditional  # 带 depends_on：按填的值才出现，算半格

    @property
    def height(self):
        base = HEIGHT.get(self.fieldtype, 1) + (0.4 if self.described else 0)
        return base / 2 if self.conditional else base


# 场景：同一张表在「新建 / 可改」与「只读详情」下显隐不同（`hidden: editable` 与 `hidden: !editable` 互斥）。
# 字段数组里 `hidden:` 与条件展开 `...(flag ? [...] : [...])` 用到的简单开关（`editable`、`!is_new`、`state.editable`）
# 各取真 / 假算一遍；别的表达式一律按「显示」算。ENV 是当前这一遍的取值。
ENV: dict[str, bool] = {}


def flag_of(value_tokens):
    """`editable` / `!is_new` / `state.editable` → (取反, 名字)；别的表达式 → None。"""
    toks = list(value_tokens)
    neg = bool(toks) and toks[0][1] == "!"
    toks = toks[1:] if neg else toks
    if not toks or toks[0][0] != "id" or len(toks) % 2 == 0:
        return None
    if any(t[1] != "." for t in toks[1::2]) or any(t[0] != "id" for t in toks[::2]):
        return None
    return neg, toks[-1][1]


def truth(value_tokens):
    """开关表达式在当前场景下的真假；不是开关 → None。"""
    flag = flag_of(value_tokens)
    if flag is None or flag[1] not in ENV:
        return None
    return ENV[flag[1]] != flag[0]


def hidden(value_tokens):
    if len(value_tokens) == 1 and value_tokens[0][0] in ("id", "p") and value_tokens[0][1] in ("1", "true"):
        return True
    return truth(value_tokens) is True


def ternary(tokens):
    """`cond ? A : B` → (cond, A, B)；不是三元 → None。"""
    q = next((i for i, t in enumerate(tokens) if t[1] == "?"), None)
    c = next((i for i, t in enumerate(tokens) if t[1] == ":" and q is not None and i > q), None)
    return (tokens[:q], tokens[q + 1:c], tokens[c + 1:]) if q is not None and c is not None else None


def flags(group):
    """字段数组里出现的简单开关名。"""
    found = set()
    for item in walk(group[2]):
        if item[0] != "grp":
            continue
        if item[1] == "{":
            props = obj_props(item)
            if "hidden" in props and (flag := flag_of(props["hidden"][1])):
                found.add(flag[1])
        if item[1] == "(" and (parts := ternary(item[2])) and (flag := flag_of(parts[0])):
            found.add(flag[1])
    return found


def field_from_object(group, line_of):
    props = obj_props(group)
    fieldtype = props.get("fieldtype", (None,))[0]
    if not isinstance(fieldtype, str) or fieldtype not in FIELDTYPES:
        return None
    if "hidden" in props and hidden(props["hidden"][1]):
        return "hidden"
    name = props.get("fieldname", ("",))[0]
    return Field(fieldtype, name if isinstance(name, str) else "", "description" in props, line_of(group[3]),
                 "depends_on" in props)


# 只产出分节 / 分栏的小工具（`u.column = () => ({fieldtype: "Column Break"})`）：按定义认，见 helpers()
BREAK_HELPERS: dict[str, str] = {}
LOCAL_HELPERS: dict[str, str] = {}
HELPER_DEF = re.compile(r"\b(\w+)\s*=\s*\([^()]*\)\s*=>\s*\(\{\s*fieldtype:\s*\"(Section Break|Column Break)\"")
MEMBER_DEF = re.compile(r"\.(\w+)\s*=\s*\([^()]*\)\s*=>\s*\(\{\s*fieldtype:\s*\"(Section Break|Column Break)\"")
MEMBER_ALIAS = re.compile(r"\b(\w+)\s*=\s*\w+\.(\w+)\s*(?=[,;\n])")


def field_from_call(part, line_of):
    """`f("名", "标签", "Data", {…})` / `u.field(…)` 这类小工具：第一个字符串是字段名，参数里认得出的类型是字段类型。"""
    at = 0
    while at + 2 < len(part) and part[at][0] == "id" and part[at + 1][1] == "." and part[at + 2][0] == "id":
        at += 2  # u.field / p.x.y
    if len(part) < at + 2 or part[at][0] != "id" or part[at + 1][0] != "grp" or part[at + 1][1] != "(":
        return None
    callee = part[at][1]
    if callee in LOCAL_HELPERS:
        return Field(LOCAL_HELPERS[callee], "", False, line_of(part[at][2]))
    args = split_commas(part[at + 1][2])
    strings = [a[0][1] for a in args if len(a) == 1 and a[0][0] == "str"]
    kinds = [s for s in strings if s in FIELDTYPES]
    if not strings or not kinds:
        return None
    extra = next((a[0] for a in args if a and a[0][0] == "grp" and a[0][1] == "{"), None)
    props = obj_props(extra) if extra else {}
    if "hidden" in props and hidden(props["hidden"][1]):
        return "hidden"
    return Field(kinds[-1], strings[0], "description" in props, line_of(part[0][2]), "depends_on" in props)


def elements(group, line_of, depth=0):
    """数组的每一项 → Field / 'unknown'（认不出的一项，按一行算）/ 'spread'（展开调用结果，不计高度）；条件展开按场景取一支。"""
    out = []
    for part in split_commas(group[2]):
        if not part:
            continue
        if part[0][1] == "..." and len(part) > 1:
            branch = part[1:]
            if branch[0][0] == "grp" and branch[0][1] == "(" and (parts := ternary(branch[0][2])):
                pick = truth(parts[0])
                branch = parts[2] if pick is False else parts[1]
            inner = next((g for g in walk(branch) if g[0] == "grp" and g[1] == "["), None)
            if inner is not None and depth < 3:
                out += elements(inner, line_of, depth + 1)
            else:
                out.append("spread")  # `...roles_fields(…)` 这类：多半自带分节，看不出几行，不计高度
            continue
        head = part[0]
        if head[0] == "grp" and head[1] == "{":
            got = field_from_object(head, line_of)
        else:
            got = field_from_call(part, line_of)
            if got is None:
                # `cond ? {…} : null` / `cond && {…}`：取对象那一支
                obj = next((g for g in part if g[0] == "grp" and g[1] == "{"), None)
                got = field_from_object(obj, line_of) if obj else None
        if got == "hidden":
            continue
        out.append(got if got is not None else "unknown")
    return out


def walk(items):
    for item in items:
        yield item
        if item[0] == "grp":
            yield from walk(item[2])


def sections(items):
    """[Field…] → [[栏…]…]，每栏是字段列表。"""
    result, columns = [], [[]]
    for item in items:
        if isinstance(item, Field) and item.fieldtype in ("Section Break", "Tab Break"):
            result.append(columns)
            columns = [[]]
        elif isinstance(item, Field) and item.fieldtype == "Column Break":
            columns.append([])
        else:
            columns[-1].append(item)
    result.append(columns)
    return [cols for cols in result if any(cols)]


def height(column):
    return sum(f.height if isinstance(f, Field) else 0 if f == "spread" else 1 for f in column)


def problems(form_sections):
    found = []
    for index, columns in enumerate(form_sections):
        columns = [c for c in columns if c] or columns
        if len(columns) > MAX_COLUMNS:
            found.append(("columns", index, f"{len(columns)} columns"))
        if len(columns) >= 2:
            heights = [height(c) for c in columns]
            if max(heights) - min(heights) > LIMIT:
                found.append(("balance", index, "column heights " + " / ".join(f"{h:g}" for h in heights)))
    for a, b in PAIRS:
        where = {}
        for index, columns in enumerate(form_sections):
            for col, column in enumerate(columns):
                top = 0.0
                for pos, item in enumerate(column):
                    if isinstance(item, Field) and item.fieldname in (a, b):
                        where[item.fieldname] = (index, col, top, pos)
                    top += height([item])
        if a in where and b in where:
            (sa, ca, ta, pa), (sb, cb, tb, pb) = where[a], where[b]
            # 同一栏上下挨着算一对（个人信息页姓在名下面，是用户 10-31 定的）；左右并排的，还要手机上叠成单列后挨着：
            # 前一格是左栏最后一格、后一格是右边那栏第一格（11-01 真机验收：添加用户名姓并排，手机上中间夹了用户名）
            cols = form_sections[sa] if sa == sb else []
            (first, cf, pf), (second, cs, ps) = sorted([(a, ca, pa), (b, cb, pb)], key=lambda x: (x[1], x[2]))
            beside = (sa == sb and cs == cf + 1 and abs(ta - tb) < 0.5 and pf == len(cols[cf]) - 1 and ps == 0)
            ok = beside or (sa == sb and ca == cb and abs(pa - pb) == 1)
            if not ok:
                found.append((f"pair:{a}|{b}", sa, f"{a} | {b} not on one row"))
    return found


# ── 收集表单 ─────────────────────────────────────────────────────────────────────────────────────
def js_files():
    for path in sorted((ROOT / "apps").rglob("*.js")):
        rel = path.relative_to(ROOT)
        if SKIP_PARTS & set(rel.parts) or path.name.endswith(".min.js") or "public" not in rel.parts and "doctype" not in rel.parts:
            continue
        yield path


def helpers(paths):
    """全仓挂在命名空间上的分节 / 分栏小工具（`u.column = () => …`，各文件共用）。"""
    for path in paths:
        text = path.read_text(encoding="utf-8", errors="replace")
        BREAK_HELPERS.update(dict(MEMBER_DEF.findall(text)))


def local_helpers(text):
    """本文件自己的小工具（`const row = () => …`）和别名（`const col = u.column`）。"""
    found = dict(BREAK_HELPERS)
    found.update(dict(HELPER_DEF.findall(text)))
    for alias, target in MEMBER_ALIAS.findall(text):
        if target in BREAK_HELPERS:
            found.setdefault(alias, BREAK_HELPERS[target])
    return found


def js_forms(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    LOCAL_HELPERS.clear()
    LOCAL_HELPERS.update(local_helpers(text))
    if "Column Break" not in text and not any(re.search(rf"\b{re.escape(h)}\(", text) for h in LOCAL_HELPERS):
        return []
    starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def line_of(pos):
        lo, hi = 0, len(starts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if starts[mid] <= pos:
                lo = mid
            else:
                hi = mid - 1
        return lo + 1

    forms, seen = [], set()
    for group in walk(tree(tokens(text))):
        if group[0] != "grp" or group[1] != "[":
            continue
        ENV.clear()
        items = elements(group, line_of)
        if not any(isinstance(i, Field) and i.fieldtype == "Column Break" for i in items):
            continue
        # 嵌在更大字段数组里的条件分支（已按展开算进外层）不再单独算
        if any(s <= group[3] <= e for s, e in seen):
            continue
        seen.add((group[3], last_pos(group)))
        name = first_name(items)
        same = sum(1 for f in forms if f["form"].split("#")[0] == name)
        name = f"{name}#{same + 1}" if same else name  # 同一文件里第一个字段相同的两张表（新建页 / 详情页）按先后编号
        names = sorted(flags(group))[:3]
        scenarios = {}
        for bits in itertools.product((True, False), repeat=len(names)):
            ENV.clear()
            ENV.update(zip(names, bits))
            label = ",".join(f"{n}={int(b)}" for n, b in zip(names, bits))
            scenarios[label] = sections(elements(group, line_of))
        ENV.clear()
        forms.append({"file": str(path.relative_to(ROOT)), "line": line_of(group[3]), "form": name, "scenarios": scenarios})
    return forms


def last_pos(group):
    pos = group[3]
    for item in walk(group[2]):
        pos = max(pos, item[2] if item[0] != "grp" else item[3])
    return pos


def first_name(items):
    return next((i.fieldname for i in items if isinstance(i, Field) and i.fieldname
                 and i.fieldtype not in STRUCTURE), "")


# 有人用原生表单打开的 DocType：控制面管理后台（侧栏那几项和它们链过去的台账），以及除系统管理员外还给了产品角色读权限的。
# 产品里的业务单据都走 Desk 页面与弹窗（上面的前端字段数组），它们的 DocType 只开给 System Manager，产品用户打不开原生表单。
# 发号单（Pearnly Issue）不动：发号页与发号表单用户交代过不改。
SKIP_DOCTYPES = {"Pearnly Issue"}


def user_facing(path, data):
    if data.get("name") in SKIP_DOCTYPES:
        return False
    if "pearnly_control" in path.parts:
        return True
    return any(row.get("read") and row.get("role") != "System Manager" for row in data.get("permissions") or [])


def doctype_forms():
    for path in sorted((ROOT / "apps").rglob("doctype/*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("doctype") != "DocType" or data.get("istable"):
            continue
        if not user_facing(path, data):
            continue
        fields = data.get("fields") or []
        if not any(f.get("fieldtype") == "Column Break" for f in fields):
            continue
        by_name = {f.get("fieldname"): f for f in fields}
        order = [by_name[n] for n in data.get("field_order") or [] if n in by_name] or fields
        items = [Field(f.get("fieldtype"), f.get("fieldname", ""), bool(f.get("description")), 0, bool(f.get("depends_on")))
                 for f in order if not f.get("hidden")]
        yield {"file": str(path.relative_to(ROOT)), "line": 0, "form": data.get("name"), "scenarios": {"": sections(items)}}


def all_forms():
    paths = list(js_files())
    helpers(paths)
    for path in paths:
        yield from js_forms(path)
    yield from doctype_forms()


def key(form, scenario, kind, section):
    return f"{form['file']}::{form['form']}::{kind}::{section}" + (f"@{scenario}" if scenario else "")


def form_problems(form):
    """[(键, 说明)]；各场景里同一处同一类问题只报一次（取第一个场景）。"""
    out, seen = [], set()
    for scenario, form_sections in form["scenarios"].items():
        for kind, section, detail in problems(form_sections):
            names = section_names(form_sections[section])
            if (kind, names) in seen:
                continue
            seen.add((kind, names))
            out.append((key(form, scenario, kind, section), detail))
    return out


def section_names(columns):
    return tuple(tuple(i.fieldname if isinstance(i, Field) else "?" for i in column) for column in columns)


def describe(form):
    lines = [f"{form['file']}:{form['line']}  ({form['form']})"]
    shown = set()
    for scenario, form_sections in form["scenarios"].items():
        signature = tuple(section_names(c) for c in form_sections)
        if signature in shown:
            continue
        shown.add(signature)
        if scenario:
            lines.append(f"  [{scenario}]")
        for index, columns in enumerate(form_sections):
            cols = []
            for column in columns:
                names = [(i.fieldname or i.fieldtype) if isinstance(i, Field) else "?" for i in column]
                cols.append(f"[{height(column):g}] " + ", ".join(names))
            lines.append(f"  §{index}: " + "  ||  ".join(cols))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--update", action="store_true")
    args = parser.parse_args()

    baseline = json.loads(BASELINE.read_text(encoding="utf-8")) if BASELINE.exists() else {}
    current, forms = {}, list(all_forms())
    for form in forms:
        current.update(form_problems(form))
    if args.list:
        for form in forms:
            print(describe(form))
            for k, detail in form_problems(form):
                print(f"  !! {k.split('::', 2)[2]}: {detail}")
        print(f"\n{len(forms)} forms with columns, {len(current)} problems")
        return 0
    new = sorted(k for k in current if k not in baseline)
    if args.update:
        if new:
            print("refusing to grow the baseline:\n  " + "\n  ".join(new))
            return 1
        kept = {k: v for k, v in baseline.items() if k in current}
        BASELINE.write_text(json.dumps(kept, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"baseline {len(baseline)} -> {len(kept)}")
        return 0
    stale = sorted(k for k in baseline if k not in current)
    for k in new:
        print(f"form layout: {k}  {current[k]}")
    if stale:
        print("fixed, remove from the baseline with --update:\n  " + "\n  ".join(stale))
    if new or stale:
        print("\n规矩：每栏一类、各栏长度接近、成对字段同一行；见 docs/work/2026-11-01-form-layout-audit.md")
        return 1
    print(f"form layout ok: {len(forms)} forms with columns, {len(baseline)} in the baseline")
    return 0


if __name__ == "__main__":
    sys.exit(main())
