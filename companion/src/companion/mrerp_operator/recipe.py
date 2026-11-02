# -*- coding: utf-8 -*-
"""MR.ERP 配方：每种单（赊购货 / 赊购费用 / 赊销 / 现销）在网页上怎么录（数据在 ``recipes/mrerp-<版本>.json``）。

引擎是通用的：列表（菜单号）→ 新增 → 表头按格子 id 填 / 选 → 明细逐行 → 合计对上才存 → 存档 → 列表找回这张 → 查看页核对。
不同单种只差配方里的几样（模块目录、对方格、表头有哪些格、收款区……）。格子按 MR.ERP 源码取；**没在真站点核对过的项
``verified: false``（待探路核对）**：这种单默认不录（``supported`` 为假，回「不支持」），只有开发开关打开才拿来跑假站点与探路。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

RECIPES = Path(__file__).resolve().parent / "recipes"
DEFAULT_VERSION = "2026.11-src"
KINDS = {"text", "date", "check", "pick"}
DRAFT_KEYS = {"docnum", "date", "cash", "vat_rate", "party_code", "credit_days", "ref_no", "ref_date", "delivery_date"}
OUTCOMES = {"kicked", "login_failed", "blocked"}


class RecipeError(ValueError):
    """配方文件本身写错了（开发问题，不是用户问题）。"""


@dataclass(frozen=True)
class Form:
    doc_type: str
    spec: Dict  # forms[doc_type]
    module_spec: Dict  # modules[module]

    @property
    def module(self) -> str:
        return self.spec["module"]

    @property
    def verified(self) -> bool:
        """这种单整条路都在真站点核对过：单种、模块、表头每一格、明细都标了 verified。"""
        parts = [self.spec, self.module_spec, self.module_spec["lines"], *self.module_spec["header"]]
        return all(bool(p.get("verified")) for p in parts)

    @property
    def cash(self) -> bool:
        return bool(self.spec.get("cash"))

    @property
    def header(self) -> List[Dict]:
        return list(self.module_spec["header"])

    @property
    def lines(self) -> Dict:
        return self.module_spec["lines"]

    @property
    def totals(self) -> Dict[str, str]:
        return self.module_spec["totals"]

    def line_id(self, name: str, n: int) -> str:
        return self.lines[name].replace("{n}", str(int(n)))

    def form_path(self) -> str:
        return self.module_spec["form"]

    def view_path(self, record_id: str) -> str:
        return self.module_spec["view"].replace("{id}", str(record_id))

    def required_ids(self) -> List[str]:
        """探路时新增页上必须找得到的格子：表头每一格、第一行明细、合计、存档按钮、单号格、对方格。"""
        out = [f["id"] for f in self.header]
        out += [self.line_id(name, 1) for name in ("code", "qty", "price", "total")]
        out += list(self.totals.values()) + [self.module_spec["save_id"], self.module_spec["party_id"],
                                             self.module_spec["docnum"]["prefix_id"],
                                             self.module_spec["docnum"]["number_id"], self.lines["add_button"]]
        receipt = self.module_spec.get("receipt") or {}
        out += [receipt[k] for k in ("cash_id", "total_id") if receipt.get(k)]
        return list(dict.fromkeys(out))


class Recipe:
    def __init__(self, data: Dict):
        self.data = data
        self.version = str(data["recipe_version"])
        self.login: Dict = data["login"]
        self.list: Dict = data["list"]
        self.modules: Dict = data["modules"]
        self.dialogs = [dict(rule, rx=re.compile(rule["match"])) for rule in data["dialogs"]]
        self.forms = {t: Form(t, spec, self.modules.get(spec.get("module"), {})) for t, spec in data["forms"].items()}
        self.saved_mark = str(data.get("saved_mark") or "")
        self.save: Dict = dict(data.get("save") or {})
        timing = data.get("timing") or {}
        self.settle_s = float(timing.get("settle_s", 3.0))
        self.save_s = float(timing.get("save_s", 60.0))
        self.list_s = float(timing.get("list_s", 20.0))
        self._validate()

    def _validate(self) -> None:
        for key in ("page", "user_id", "password_id", "submit_id", "select_page", "account_link", "logout_page", "footer"):
            if key not in self.login:
                raise RecipeError(f"login 缺 {key}")
        for key in ("view", "search_id", "search_button", "rows", "add_id"):
            if key not in self.list:
                raise RecipeError(f"list 缺 {key}")
        for form in self.forms.values():
            if not form.module_spec:
                raise RecipeError(f"{form.doc_type} 的模块 {form.spec.get('module')} 没有定义")
            for key in ("form", "view", "docnum", "header", "lines", "totals", "save_id", "party_id"):
                if key not in form.module_spec:
                    raise RecipeError(f"{form.module} 缺 {key}")
            for field in form.header:
                if field.get("kind") not in KINDS:
                    raise RecipeError(f"{form.module}.{field.get('id')} 的 kind 不认识")
                key = str(field.get("key") or "")
                if key not in DRAFT_KEYS and not key.startswith("defaults."):
                    raise RecipeError(f"{form.module}.{field.get('id')} 的取值 {key} 不认识")
            for name in ("add_button", "code", "qty", "price", "total"):
                if name not in form.lines:
                    raise RecipeError(f"{form.module}.lines 缺 {name}")
            for name in ("before_vat", "vat", "net"):
                if name not in form.totals:
                    raise RecipeError(f"{form.module}.totals 缺 {name}")
        for rule in self.dialogs:
            if rule.get("outcome") not in OUTCOMES:
                raise RecipeError(f"弹框 {rule.get('name')} 的处理不认识")

    def form(self, doc_type: str) -> Optional[Form]:
        return self.forms.get(str(doc_type or "").lower())

    def supported(self, doc_type: str, *, allow_unverified: bool = False) -> bool:
        form = self.form(doc_type)
        return bool(form and (form.verified or allow_unverified))

    def match_dialog(self, text: str) -> Optional[Dict]:
        for rule in self.dialogs:
            if rule["rx"].search(str(text or "").strip()):
                return rule
        return None

    def unverified(self) -> List[str]:
        """所有标着「待探路核对」的项（记录 / 汇报用）。"""
        out = []
        if not self.login.get("verified"):
            checked = set((self.login.get("checked") or {}).get("keys") or [])
            out += [f"login.{k}" for k in self.login if not k.startswith("_") and k not in checked
                    and k not in ("verified", "checked")]
        if not self.list.get("verified"):
            out.append("list")
        for name, module in self.modules.items():
            if not module.get("verified"):
                out.append(f"modules.{name}")
            out += [f"modules.{name}.header.{f['id']}" for f in module.get("header", []) if not f.get("verified")]
            if not (module.get("lines") or {}).get("verified"):
                out.append(f"modules.{name}.lines")
            if module.get("receipt") and not module["receipt"].get("verified"):
                out.append(f"modules.{name}.receipt")
        out += [f"forms.{t}" for t, form in self.forms.items() if not form.spec.get("verified")]
        out += [f"dialogs.{r['name']}" for r in self.dialogs if not r.get("verified")]
        if self.save and not self.save.get("verified"):
            out.append("save")
        return out


def load(version: str = DEFAULT_VERSION) -> Recipe:
    path = RECIPES / f"mrerp-{version}.json"
    if not path.is_file():
        raise RecipeError(f"没有 MR.ERP {version} 的配方")
    return Recipe(json.loads(path.read_text(encoding="utf-8")))


def versions() -> List[str]:
    return sorted(p.stem[len("mrerp-"):] for p in RECIPES.glob("mrerp-*.json"))
