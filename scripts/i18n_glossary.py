# -*- coding: utf-8 -*-
"""术语表的读写：每个产品维护自己那一份，生成脚本只负责汇总。

术语表原来是**一个** `scripts/i18n/messages.tsv`，一条文案一行，用 `app` 列说明
它属于哪个 App。三个产品一起改，两份工作流就会在同一张表里撞车。

现在术语表按 App 拆开，放在**该 App 自己**的目录里：

    apps/<app>/<app>/i18n/messages.tsv      # 表头：en / zh / th / ja / key

`app` 不再是一列 —— 文件放在谁家就是谁的。译文落在哪个 App 的翻译包里仍由 `app`
决定：租户站点不装 `pearnly_control`，所以控制面的文案要落到 `pearnly_core` 才渲染
得到（这一条没有变）。

两条硬规矩（沿用原表）：

· **msgid 一律英文**。`_()` 找不到译文时原样返回 msgid，中文当 msgid 就等于
  "换语言永远是中文"。
· **`key` 列可选**：填了表示这条是语义键（翻译包第一列用 key，`en` 列当英文文案）；
  不填就是 Frappe 那套"英文原文当 msgid"。

供 `i18n-build.py`（术语表 → 翻译包）与 `i18n-apply.py`（代码里的中文 → msgid）共用。
"""

from __future__ import annotations

import csv
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# App 名 = 目录名 = 包名（五个都是这样）
APPS = ("pearnly_core", "pearnly_control", "pearnly_cowork", "pearnly_dms",
        "pearnly_erp_portal")

LANGS = ("zh", "th", "ja")
# 也要出一份 `en.csv`：Frappe 那套里"英文就是 msgid 本身"、不需要文件；
# 但**业务前端**是按 key 取文案的（`nav.home` 这种），英语那一档没有文件
# 就会把 key 直接显示出来。所以英文也要有包。
EN = "en"
FIELDS = (EN,) + LANGS + ("key",)
HEADER = "\t".join(FIELDS)

# 拆表之前的路径。留着只是为了**明确拒绝**它：那份文件已经不生成了，
# 谁再往里加行都不会生效（静默丢译文是最难查的一类问题）。
RETIRED = os.path.join(HERE, "i18n", "messages.tsv")


def source_path(app):
    """某个 App 术语表的绝对路径。"""
    return os.path.join(ROOT, "apps", app, app, "i18n", "messages.tsv")


def read_glossary():
    """读全部产品的术语表，返回 (行, 问题)。行里带 `app`（由文件位置决定）。"""
    problems = []
    if os.path.exists(RETIRED):
        problems.append(
            "%s 已停用：术语表按产品拆到 apps/<app>/<app>/i18n/messages.tsv 了，"
            "往旧表加行不会生效" % os.path.relpath(RETIRED, ROOT)
        )
    rows = []
    seen = {}
    for app in APPS:
        path = source_path(app)
        if not os.path.exists(path):
            problems.append("缺 %s" % os.path.relpath(path, ROOT))
            continue
        rows.extend(_read_one(app, path, seen, problems))
    return rows, problems


def _read_one(app, path, seen, problems):
    rel = os.path.relpath(path, ROOT)
    out = []
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if tuple(reader.fieldnames or ()) != FIELDS:
            problems.append("%s: 表头必须是 %s，实际是 %s" % (rel, HEADER, reader.fieldnames))
            return []
        for index, raw in enumerate(reader, start=2):
            # `key` 可以留空（那表示用英文原文当 msgid），其余列必填
            missing = [f for f in FIELDS if f != "key" and not str(raw.get(f) or "").strip()]
            if missing:
                problems.append("%s 第 %d 行缺列：%s" % (rel, index, ", ".join(missing)))
                continue
            # `\n` 是转义写法（TSV 一行放不下真换行），出译文时要还原
            item = {f: str(raw.get(f) or "").replace("\\n", "\n").strip() for f in FIELDS}
            item["app"] = app
            item["msgid"] = item["key"] or item[EN]
            key = (app, item["msgid"])
            if key in seen:
                # 同一个英文 msgid 允许出现多行（两个中文说法对应同一个英文，
                # 例如「明细」和「细节」都翻 Details）—— 但译文必须一致，
                # 否则生成时谁覆盖谁全看顺序，那种不一致要挡下来。
                if any(seen[key][lang] != item[lang] for lang in LANGS):
                    problems.append(
                        "%s 第 %d 行与前面的 %s 同英文但译文不同：%s"
                        % (rel, index, key[1], seen[key]["zh"])
                    )
                continue
            seen[key] = item
            out.append(item)
    return out
