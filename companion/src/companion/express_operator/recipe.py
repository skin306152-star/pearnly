# -*- coding: utf-8 -*-
"""配方：每种 Express 表单怎么录（数据在 ``recipes/express-<版本>.json``）。

引擎是通用的：菜单 → 新建 → 表头按回车走格、按格子左边的标签填 → 点进明细表格逐格录 → 合计核对 → 存档确认。
不同单种只差配方里的几样（菜单路径、要填哪些标签、单号格叫什么……）。新账套第一次先探路（``probe``），
核对这本账套的表头标签和配方对得上，对不上就这种单暂停、报开发补配方，不拿旧配方硬录。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

RECIPES = Path(__file__).resolve().parent / "recipes"
DEFAULT_VERSION = "1.0.1.94"
VALUE_KEYS = {"date", "party", "ref", "vat_mode"}


class RecipeError(ValueError):
    """配方文件本身写错了（开发问题，不是用户问题）。"""


@dataclass(frozen=True)
class Form:
    doc_type: str
    spec: Dict

    @property
    def verified(self) -> bool:
        return bool(self.spec.get("verified"))

    @property
    def menu(self) -> List[str]:
        return [str(k) for k in self.spec["menu"]]

    @property
    def title(self) -> str:
        return self.spec["title"]

    @property
    def header(self) -> Dict:
        return self.spec["header"]

    @property
    def values(self) -> Dict[str, str]:
        return self.header["values"]

    @property
    def stop_after(self) -> str:
        return self.header["stop_after"]

    @property
    def docnum_label(self) -> str:
        return self.spec["docnum_label"]

    def required_labels(self) -> List[str]:
        """探路时这本账套必须找得到的标签：要填的格、走到哪格为止、单号格。"""
        out = list(self.values) + [self.stop_after, self.docnum_label]
        return list(dict.fromkeys(out))


class Recipe:
    def __init__(self, data: Dict):
        self.data = data
        self.version = data["express_version"]
        self.commands: Dict[str, int] = {k: int(v) for k, v in data["commands"].items()}
        self.dialogs = [dict(r, rx=re.compile(r["match"])) for r in data["dialogs"]]
        self.forms = {t: Form(t, spec) for t, spec in data["forms"].items()}
        self.coverage: Dict[str, int] = {k: int(v) for k, v in data.get("coverage", {}).items() if not k.startswith("_")}
        self.form_open_ms = int(data.get("form_open_ms", 6500))
        self.save_wait_ms = int(data.get("save_wait_ms", 2500))
        self.login = data.get("login") or {}
        self._validate()

    def _validate(self) -> None:
        for name in ("new", "save", "cancel", "void"):
            if name not in self.commands:
                raise RecipeError(f"commands 缺 {name}")
        for form in self.forms.values():
            for key in ("menu", "title", "header", "grid_offset", "unit_dialog", "totals", "docnum_label"):
                if key not in form.spec:
                    raise RecipeError(f"{form.doc_type} 缺 {key}")
            bad = set(form.values.values()) - VALUE_KEYS
            if bad:
                raise RecipeError(f"{form.doc_type} 表头取值不认识：{sorted(bad)}")
            if form.stop_after not in form.header.get("labels", [form.stop_after]):
                raise RecipeError(f"{form.doc_type} stop_after 不在 labels 里")
            if form.doc_type not in self.coverage:
                raise RecipeError(f"{form.doc_type} 没登记分期")

    def form(self, doc_type: str) -> Optional[Form]:
        return self.forms.get(doc_type.upper())

    def supported(self, doc_type: str) -> bool:
        form = self.form(doc_type)
        return bool(form and form.verified)

    def phase(self, doc_type: str) -> Optional[int]:
        return self.coverage.get(doc_type.upper())

    def match_dialog(self, text: str) -> Optional[Dict]:
        for rule in self.dialogs:
            if rule["rx"].search(text):
                return rule
        return None


def load(version: str = DEFAULT_VERSION) -> Recipe:
    path = RECIPES / f"express-{version}.json"
    if not path.is_file():
        raise RecipeError(f"没有 Express {version} 的配方")
    return Recipe(json.loads(path.read_text(encoding="utf-8")))


def versions() -> List[str]:
    return sorted(p.stem[len("express-"):] for p in RECIPES.glob("express-*.json"))
