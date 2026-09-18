# -*- coding: utf-8 -*-
"""把老产品**编辑器页**的四语文案导进 `pearnly_dms/translations/*.csv`。

老产品 `static/dms-booking-edit/dms-booking-i18n.js`（线上基准 `501589f0`）里那份
`TEXT` 是 **4 语言 x 82 键**、且**没有一条缺译**（th/en/zh/ja 键集完全一致，本脚本会核）。
它是键式的，所以能逐字搬过来 —— 零翻译成本、零窜改风险。

搬的规则：

  · 老键 `save` -> 新键 `dms.edit.save`（`dms.edit.` 是本页的命名空间）；
  · 值**逐字照搬**，一个字符都不改（改了就跟线上那套用过的话术对不上）；
  · 语言集就是老产品那四种 —— 不多建一档，也不留空档；
  · 顺手删掉线上**已退役**的逐字段编辑那批键（`dms.card.edit.*` / `TXT_EDIT_PROMPT` /
    `BTN_EDIT_CANCEL` / `TXT_EDIT_PICK`）：点 [แก้ไข] 早就改成"直接开浏览器编辑页"了，
    留着就是在语言包里留一段死代码。

另有 1 条**不是从老包来的**：`dms.card.TXT_EDIT_UNAVAILABLE`。老产品把这句话写在代码里
（`customer_edit_entry._open` 的泰文常量），按本仓"代码里只留 key"的规矩得进包，
泰文照抄线上那句，其余三语由本次补齐。

用法：

    python3 scripts/import-legacy-edit-i18n.py --source <老仓库>/static/dms-booking-edit/dms-booking-i18n.js

幂等：重复跑结果一样（先删同名键，再按老包的键顺序追加）。
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TRANSLATIONS = os.path.join(ROOT, "apps", "pearnly_dms", "pearnly_dms", "translations")
LANGUAGES = ("th", "en", "zh", "ja")
PREFIX = "dms.edit."

# 线上已退役的逐字段编辑键（新仓库里已无任何调用方，见 `dms_cards._edit_button`）。
RETIRED = re.compile(
    r"^dms\.card\.(edit\.[a-z_]+|TXT_EDIT_PROMPT|BTN_EDIT_CANCEL|TXT_EDIT_PICK)$"
)

# 老产品写在代码里、按新规矩要进包的那一句（泰文＝线上原文）。
TXT_EDIT_UNAVAILABLE = {
    "th": "ยังเปิดหน้าแก้ไขไม่ได้ กรุณาลงใหม่อีกครั้งครับ",
    "en": "The edit page could not be opened. Please start again from LINE.",
    "zh": "暂时打不开编辑页，请从 LINE 重新进入。",
    "ja": "編集ページを開けませんでした。LINE からもう一度お試しください。",
}


def read_legacy(path: str) -> dict:
    """用 node 读那份 JS（它把 TEXT 挂在 window 上），比手写解析可靠。"""
    script = (
        "global.window={};"
        f"require({json.dumps(path)});"
        "process.stdout.write(JSON.stringify(global.window.DMS_BOOKING_TEXT));"
    )
    out = subprocess.run(["node", "-e", script], check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def load_rows(path: str) -> list:
    """读成行列表，并**记下这个文件原本的换行**。

    ⚠️ 这几个 CSV 的换行并不统一：`th.csv` 是 LF，而 `en/zh/ja.csv` 是 **CRLF**。
    整份重写时若一律写 LF，diff 会把**每一行**都算成改动 —— 真正的增删就被埋在噪音里了。

    而且要按 csv 的规矩读：包里有**跨行的引号字段**（`dms.card.TXT_MENU_GREETING` 是
    三行文案）。先按行切开再解析会把那条压成一行（踩过一次）。
    """
    with open(path, "rb") as handle:
        raw = handle.read()
    terminator = "\r\n" if b"\r\n" in raw else "\n"
    text = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8", newline="")
    rows = [row for row in csv.reader(text) if row]
    return rows, terminator


def write_rows(path: str, rows: list, terminator: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        csv.writer(handle, lineterminator=terminator, quoting=csv.QUOTE_MINIMAL).writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, help="老产品的 dms-booking-i18n.js")
    args = parser.parse_args()

    legacy = read_legacy(args.source)
    missing = [lang for lang in LANGUAGES if lang not in legacy]
    if missing:
        print("老包里缺语言: %s" % missing, file=sys.stderr)
        return 2
    base = set(legacy["th"])
    for lang in LANGUAGES:
        diff = base.symmetric_difference(set(legacy[lang]))
        if diff:
            print("老包 %s 的键集与 th 不一致: %s" % (lang, sorted(diff)), file=sys.stderr)
            return 2

    for lang in LANGUAGES:
        path = os.path.join(TRANSLATIONS, f"{lang}.csv")
        rows, terminator = load_rows(path)
        kept = [row for row in rows if not RETIRED.match(row[0])]
        removed = len(rows) - len(kept)
        # 先清掉上次导入可能留下的同名键（幂等），再按老包的键顺序追加。
        kept = [row for row in kept if not row[0].startswith(PREFIX)]
        for key in legacy["th"]:
            kept.append([f"{PREFIX}{key}", legacy[lang][key]])
        kept.append(["dms.card.TXT_EDIT_UNAVAILABLE", TXT_EDIT_UNAVAILABLE[lang]])
        write_rows(path, kept, terminator)
        print(
            "%s: 删退役键 %d 条，写入 %s%d 条 + TXT_EDIT_UNAVAILABLE"
            % (lang, removed, PREFIX, len(legacy["th"]))
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
