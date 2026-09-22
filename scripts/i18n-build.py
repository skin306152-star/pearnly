# -*- coding: utf-8 -*-
"""从术语表生成各 App 的 Frappe 翻译包（`apps/<app>/<app>/translations/<lang>.csv`）。

为什么要有这层生成：同一条文案在三个地方都要用到（服务端 `_()`、Desk `__()`、
DocType JSON 的 label），各写一份必然对不上。所以**术语表是唯一来源**，而术语表
**按产品各自维护**在 `apps/<app>/<app>/i18n/messages.tsv`（一条文案一行，四个语言
并排）；这里只负责把它们汇总成翻译包。

生成的 CSV 是 Frappe 认的标准格式（`msgid,译文`），也是**要提交的产物** ——
发布时读的是它，不是术语表。

几条规矩：

· **msgid 一律英文**。`_()` 找不到译文时原样返回 msgid，中文当 msgid 就等于
  "换语言永远是中文"。
· **译文放在哪个 App 由"谁渲染"决定**，不是"谁定义"。例：功能目录写在
  `pearnly_control`，但工作台卡片渲染在**租户站点**上，而租户站点不装
  `pearnly_control` —— 所以那条译文必须落在 `pearnly_core`（每个站点都装）。
  拆表之后，"放哪个 App"就是"把这一行写进谁的术语表"。
· **不删已有的行**：CSV 里可能有脚本之外手工加的条目（例如沿用老产品的译文），
  生成时合并保留，只覆盖术语表里出现过的键。
· **带 key 的词条同时按英文原文再落一行**：界面按 key 取词（`t("trade.shiftNo")`），
  而服务端 `_()` 和 DocType 字段标签认的是**英文原文**。只落 key 那一行的话，同一句话
  在界面上是中文、在导出件/Desk 表单里却是英文 —— 2026-09-23 用户口径：
  "导出的报表术语表口径要统一"。所以下面 `_aliases()` 会把"没有同英文的无 key 行、
  且同英文的几条译文一致"的带 key 词条，按英文原文再补一行。
  两种情况**不补**：已经有了同英文的无 key 行（那行才是准的，例如 `Items` 既有无 key
  的「物料」又有 `trade.lines` 的「物料明细」）；同英文的几条带 key 词条译文不一致
  （谁覆盖谁说不清，例如 `Not set` 有「未设」也有「未填」）。

用法：

    python3 scripts/i18n-build.py            # 生成（幂等）
    python3 scripts/i18n-build.py --check    # 只校验术语表，不写文件
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict

from i18n_glossary import EN, LANGS, ROOT, read_glossary



def translations_dir(app: str) -> str:
    return os.path.join(ROOT, "apps", app, app, "translations")


def existing(app: str, lang: str) -> dict:
    path = os.path.join(translations_dir(app), lang + ".csv")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8", newline="") as handle:
        return {row[0]: row[1] for row in csv.reader(handle) if len(row) >= 2 and row[0].strip()}


def write(app: str, lang: str, table: dict) -> str:
    directory = translations_dir(app)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, lang + ".csv")
    with open(path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        for msgid in sorted(table):
            writer.writerow([msgid, table[msgid]])
    return os.path.relpath(path, ROOT)


def _aliases(items: list[dict]) -> list[dict]:
    """带 key 的词条里，能安全按英文原文再补一行的那些。

    返回 `[{en, zh, th, ja, ...}]`（就是术语表那种行，只是 mdgid 用英文原文）。
    规则见文件头：无同英文的无 key 行、且同英文的带 key 行译文一致。
    """
    plain = {item[EN] for item in items if not item["key"]}
    msgids = {item["msgid"] for item in items}
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        if item["key"]:
            grouped[item[EN]].append(item)
    out = []
    for en, group in grouped.items():
        if en in plain or en in msgids:
            continue
        if len({tuple(g[lang] for lang in LANGS) for g in group}) > 1:
            continue
        row = dict(group[0])
        row["alias"] = True
        out.append(row)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验术语表，不写文件")
    args = parser.parse_args()

    rows, problems = read_glossary()
    for problem in problems:
        print("  [术语表] %s" % problem)

    by_app: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_app[row["app"]].append(row)

    print("术语表：%d 条文案，%d 个 App" % (len(rows), len(by_app)))
    for app, items in sorted(by_app.items()):
        print("  %-22s %d 条" % (app, len(items)))

    if args.check:
        print("RESULT: %s" % ("FAIL" if problems else "PASS"))
        return 1 if problems else 0

    written = []
    for app, items in sorted(by_app.items()):
        extra = _aliases(items)
        for lang in (EN,) + LANGS:
            table = existing(app, lang)
            for item in items + extra:
                msgid = item["msgid"]
                # 别名单行：msgid 用英文原文（`key` 为空的那种写法）
                if item.get("alias"):
                    msgid = item[EN]
                # 英文档：语义键的值是英文文案，英文原文当 msgid 的那种值就是它自己
                table[msgid] = item["en"] if lang == EN else item[lang]
            written.append(write(app, lang, table))
    for path in written:
        print("  写入 %s" % path)

    print("RESULT: %s" % ("FAIL" if problems else "PASS"))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
