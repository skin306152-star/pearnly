"""扫全站的三类界面约定（用户 2026-09-23："你的扫描脚本扫不全，会漏"）。

上一轮我用 `rg 'Small Text'` 找备注/地址框，结果漏掉了真正的坑：官方
`ControlSmallText.make_input()` 里写死了**内联** `height: 150px`，皮肤改不动它 ——
按字段类型 grep 是扫不出这种事的。所以这里换三条**能判对错**的规则：

1. **多行框的尺寸只能来自共用皮肤**：`textarea` 的高度/宽度只允许出现在
   `pearnly_core/public/desk/product.css`（共用那几条）与 `product.js` 的
   `p.grow_textarea`（内容超出才长）里；别的页面/皮肤再给它写死尺寸就是违规。
2. **给人看的主档一律用名字**：凡是"被别的 DocType 用 Link 指到、本身又有一个
   `*_name` 主档名"的 DocType，都必须声明 `title_field` + `show_title_field_in_link` ——
   否则同一个仓/公司/客户在有的地方显示编号（`WH-00101` / `CO-00552`）、有的地方显示名字
   （用户 2026-09-23："图三怎么显示不同名呀，全站的错误吗"）。
3. **官方网格的 `columns` 是十二等分，不是相对权重**：`grid_row.js` 直接把它拼成
   `col-xs-<columns>`，`setup_columns()` 里 `total_colsize > 10` 会打开横向滚动。
   一张表里各列的份数加起来超过 10，格子就会被裁到弹窗外面
   （用户 2026-09-23 的收银「交班」截图就是 5+4+3+3+3=18 的结果）。

另外打**清单**供人核（不是判错，是别再漏）：全站多行框定义、主档显示点、每张网格的列份数
（含"静态算不出总和"的那些，明确列出来而不是跳过）。

    python3 scripts/check-desk-ui.py            # 报告
    python3 scripts/check-desk-ui.py --strict   # 有违规就非零退出（可当闸）
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DESK_GLOBS = ["apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/*.js",
              "apps/pearnly_core/pearnly_core/public/desk/*.js",
              "apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/*.css",
              "apps/pearnly_core/pearnly_core/public/desk/*.css"]
SKIN_CSS = "apps/pearnly_core/pearnly_core/public/desk/product.css"
GROW_JS = "apps/pearnly_core/pearnly_core/public/desk/product.js"
MULTILINE = ('"Small Text"', "'Small Text'", '"Text"', "'Text'",
             '"Long Text"', '"Text Editor"')
SIZE_KEYS = ("height", "min-height", "max-height", "width", "min-width", "max-width")
# 「主档引用」的字段名：一个 Link 字段叫这些名字，说明它选的是主档（谁 / 哪个仓 / 哪件货），
# 界面必须显示名字。单据指针（sales_document / count / item / shift / return_against）不在内。
MASTER_REFERENCE_FIELDS = {
    "company", "warehouse", "location", "supplier", "customer", "product",
    "category", "subcategory", "parent_category", "unit", "terminal",
}


def desk_files():
    for pattern in DESK_GLOBS:
        yield from sorted(ROOT.glob(pattern))


def rel(path):
    return str(path.relative_to(ROOT))


def textarea_size_overrides():
    """多行框尺寸是不是只来自共用皮肤（第 1 条规则）。

    只管**多行框**：页面给普通布局容器写 `maxHeight`（列表的可视区那种）不算违规，
    上一版在这里误报过 —— 判得准比判得宽有用。
    """
    problems = []
    for path in desk_files():
        name = rel(path)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            lowered = line.lower()
            if path.suffix == ".css":
                # 只查**针对 textarea** 的规则块：往上找最近的选择器行。
                if "textarea" not in lowered or name == SKIN_CSS:
                    continue
                if any(f"{key}:" in lowered for key in SIZE_KEYS):
                    problems.append(f"{name}:{number}: 在共用皮肤之外给 textarea 定尺寸：{line.strip()}")
            else:
                if "textarea" not in lowered and "smalltext" not in lowered and "small_text" not in lowered:
                    continue
                if ".css(" not in lowered and 'style="' not in lowered and "style='" not in lowered:
                    continue
                if not any(key in lowered for key in SIZE_KEYS):
                    continue
                if name == GROW_JS and "grow_textarea" in path.read_text(encoding="utf-8"):
                    continue   # 共用层那一个"内容超出才长"是唯一允许的
                problems.append(f"{name}:{number}: 代码里给多行框写死尺寸：{line.strip()}")
    return problems


def warehouse_display_rule():
    """主档有没有"链接显示名字"（第 2 条规则）。

    判据不用人肉列名单：**被一个"主档引用"字段指到**（字段名是 company / warehouse /
    supplier / customer / product / category / terminal 这类主档名词）**并且自己有 `*_name`
    主档名**的 DocType，就要两件都声明。单据之间的指针（sales_document / count / item /
    shift / return_against）不算 —— 那些的编号本身就是要给人看的名字。这样新增一张主档表、
    或新增一个主档选择框时，规则自动覆盖到它 —— 用户说的"扫不全"就是指靠手写名单的检查。
    """
    doctypes = {}
    link_targets = {}
    for path in sorted(ROOT.glob("apps/pearnly_*/pearnly_*/pearnly_*/doctype/*/*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if data.get("doctype") != "DocType":
            continue
        doctypes[data["name"]] = (path, data)
        for field in data.get("fields") or []:
            if field.get("fieldtype") == "Link" and str(field.get("options") or "").startswith("ERP "):
                link_targets.setdefault(field["options"], set()).add(str(field.get("fieldname") or ""))

    problems, inventory = [], []
    for name in sorted(link_targets):
        if name not in doctypes:
            continue   # 不在本仓的 DocType（框架表等）
        path, data = doctypes[name]
        labels = [f["fieldname"] for f in data.get("fields") or []
                  if f.get("fieldtype") == "Data" and f.get("reqd")
                  and str(f.get("fieldname", "")).endswith("_name")]
        referring = ", ".join(sorted(link_targets[name]))
        if not any(field in MASTER_REFERENCE_FIELDS for field in link_targets[name]):
            inventory.append((name, f"非主档引用（{referring}）—— 编号即名字，不要求"))
            continue
        if not labels:
            inventory.append((name, f"主档引用（{referring}）但没有必填的 *_name 名"))
            continue
        want = labels[0]
        inventory.append((name, f"主档引用（{referring}）title_field={data.get('title_field')!r} "
                                f"show={data.get('show_title_field_in_link')!r}"))
        if data.get("title_field") != want:
            problems.append(f"{rel(path)}: {name} 的 title_field 应是 {want!r}（现在 {data.get('title_field')!r}）"
                            " —— 链接里会显示编号")
        if not data.get("show_title_field_in_link"):
            problems.append(f"{rel(path)}: {name} 缺 show_title_field_in_link —— 选择框里会显示编号")
    return problems, inventory


def grid_column_rule():
    """每张网格的列份数（第 3 条规则）。

    `columns` 是十二等分里的份数（`col-xs-<columns>`），一张表加起来 ≤10 才不会横向滚。
    静态看得见的直接判；用变量/展开写的那几张（共用 `balance_table()` 这种）算不出总和，
    单独列出来让人核 —— 不静默跳过。
    """
    problems, inventory = [], []
    for path in desk_files():
        if path.suffix != ".js":
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'fieldtype:\s*["\']Table["\']', text):
            fields_at = text.find("fields:", match.end())
            if fields_at == -1 or fields_at - match.end() > 400:
                continue
            start = text.find("[", fields_at)
            if start == -1:
                continue
            depth, end = 0, start
            for index in range(start, min(len(text), start + 4000)):
                if text[index] == "[":
                    depth += 1
                elif text[index] == "]":
                    depth -= 1
                    if depth == 0:
                        end = index
                        break
            block = text[start:end + 1]
            line = text[:match.start()].count("\n") + 1
            columns = [int(value) for value in re.findall(r"columns:\s*(\d+)", block)]
            spreads = "..." in block
            total = sum(columns)
            where = f"{rel(path)}:{line}"
            if spreads or not columns:
                inventory.append((where, f"静态算不出（有展开/变量），字面份数 {columns}"))
            elif total > 10:
                problems.append(f"{where}: 一张表列份数合计 {total} > 10（{'+'.join(str(c) for c in columns)}）"
                                " —— 格子会被裁出去")
            else:
                inventory.append((where, f"合计 {total}（{'+'.join(str(c) for c in columns)}）"))
    return problems, inventory


FULL_WIDTH_TYPES = ("Table", "HTML", "Button")


def full_width_rule():
    """整行控件有没有被 Column Break 收口（第 4 条规则）。

    Frappe 的一栏是**竖着堆**字段的：`Column Break` 只换到下一栏。表格 / HTML / 按钮这些
    整行控件如果上一个"分栏标记"是 Column Break，它就会落进半栏里被挤窄 —— 库存流水弹窗、
    收发存二级页、收银「交班」的对账表都是这么坏掉的。共用层 `p.field_rows()` 会自动给它们
    补 Section Break（`tests/test-field-rows.cjs` 钉的就是这条），这个规则查"页面有没有绕开它"。
    """
    problems = []
    for path in desk_files():
        if path.suffix != ".js":
            continue
        text = path.read_text(encoding="utf-8")
        for call in re.finditer(r"p\.(?:dialog|layout)\(", text):
            start = text.find("[", call.end())
            if start == -1 or start - call.end() > 200:
                continue
            depth, end = 0, None
            for index in range(start, len(text)):
                if text[index] == "[":
                    depth += 1
                elif text[index] == "]":
                    depth -= 1
                    if depth == 0:
                        end = index
                        break
            if end is None:
                continue
            block = text[start:end + 1]
            if "p.field_rows(" in block:
                continue   # 交给共用层排的，不在这里判
            markers = [(m.start(), m.group(1) or "") for m in re.finditer(
                r'fieldtype:\s*["\'](Column Break|Section Break|Table|HTML|Button)["\']'
                # 展开进来的那份字段（`...balance_table(...)` 这种）静态看不出类型，
                # 按"可能是整行控件"保守判：真要展开一组普通字段，就交给 p.field_rows 排。
                r"|\.\.\.[A-Za-z_$][\w$]*\(", block)]
            last_break = None
            for _, kind in markers:
                if kind == "":
                    if last_break == "Column Break":
                        line = text[:start + 1].count("\n") + 1
                        problems.append(f"{rel(path)}:{line}: 展开进来的字段跟在 Column Break 后面，"
                                        "可能被挤进半栏 —— 交给 p.field_rows() 排，或自己加 Section Break")
                elif kind in ("Column Break", "Section Break"):
                    last_break = kind
                elif kind in FULL_WIDTH_TYPES and last_break == "Column Break":
                    line = text[:start + 1].count("\n") + block[: block.find(kind)].count("\n") + 1
                    problems.append(f"{rel(path)}:{line}: {kind} 前一个是 Column Break，会被挤进半栏"
                                    " —— 交给 p.field_rows() 排，或自己加 Section Break")
    return problems


def multiline_inventory():
    """全站多行框（备注/地址）清单 —— 供人核，别再靠 grep 一种字段类型。"""
    found = []
    for path in desk_files():
        if path.suffix != ".js":
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, 1):
            if any(token in line for token in MULTILINE):
                fieldname = re.search(r'fieldname[":\s]+["\']([\w]+)["\']', line)
                if not fieldname and number > 1:
                    fieldname = re.search(r'fieldname[":\s]+["\']([\w]+)["\']', lines[number - 2])
                found.append((f"{rel(path)}:{number}", fieldname.group(1) if fieldname else "?"))
    return found


def warehouse_display_sites():
    """界面上显示仓库的地方：哪些用名字、哪些用编号。"""
    sites = []
    for pattern in ["apps/pearnly_erp_portal/pearnly_erp_portal/public/desk/*.js",
                    "apps/pearnly_core/pearnly_core/public/desk/*.js"]:
        for path in sorted(ROOT.glob(pattern)):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "warehouse_name" in line:
                    sites.append((f"{rel(path)}:{number}", "名字", line.strip()[:90]))
                elif re.search(r'\brow\.warehouse\b|\bline\.warehouse\b', line):
                    sites.append((f"{rel(path)}:{number}", "编号", line.strip()[:90]))
    return sites


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="有违规就非零退出")
    args = parser.parse_args()

    title_problems, title_inventory = warehouse_display_rule()
    grid_problems, grid_inventory = grid_column_rule()
    problems = textarea_size_overrides() + title_problems + grid_problems + full_width_rule()
    print(f"[界面扫描] 多行框定义 {len(multiline_inventory())} 处；"
          f"主档显示点 {len(title_inventory)} 个；网格 {len(grid_inventory)} 张")
    print("\n多行框（备注/地址）定义：")
    for where, fieldname in multiline_inventory():
        print(f"  · {where}  {fieldname}")
    print("\n界面上显示仓库的地方（哪些用名字、哪些用编号）：")
    for where, kind, snippet in warehouse_display_sites():
        print(f"  · [{kind}] {where}  {snippet}")
    print("\n主档（被 Link 指到的表）名字显示：")
    for name, note in title_inventory:
        print(f"  · {name:24s} {note}")
    print("\n网格列份数（十二等分，≤10 才不横向滚）：")
    for where, note in grid_inventory:
        print(f"  · {where}  {note}")
    if problems:
        print("\n✗ 违规：")
        for problem in problems:
            print("  -", problem)
    else:
        print("\n✓ 四条规则都过：多行框尺寸只来自共用皮肤；主档链接显示名字；"
              "网格列份数不超；整行控件不被 Column Break 收口。")
    return 1 if (problems and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
