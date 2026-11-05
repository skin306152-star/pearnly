# -*- coding: utf-8 -*-
"""新账套第一次先「探路」（评估 §3）：同一个 Express，不同账套的菜单、表头顺序、价格类型默认都可能不同。

每种要录的单：开表单 → 新建 → 读新建状态下所有格子的标签（不打任何字、不按回车）→ 取消（还没录明细时取消不写任何东西，Express 问
1312 → 按「ตกลง」，试点实测）。配方要用到的标签（要填的格、走到哪格为止、单号格）都找得到才算通过；对不上就这种单暂停
（recipe_mismatch），报开发补配方，不拿旧配方硬录。结果按「账套 + 配方版本 + 单种」存本机，同一本账套下次不再探。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from .engine import Engine


class ProbeStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> Dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def key(account_key: str, version: str, doc_type: str) -> str:
        return f"{account_key}|{version}|{doc_type}"

    def get(self, account_key: str, version: str, doc_type: str) -> Optional[Dict]:
        return self._load().get(self.key(account_key, version, doc_type))

    def put(self, account_key: str, version: str, doc_type: str, result: Dict) -> None:
        data = self._load()
        data[self.key(account_key, version, doc_type)] = result
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)


class MenuMemory:
    """这本账套这种单实际的菜单路径（AI 接手选对、开出了正确窗口后记下，主会话 11-05）：与探路结果同一个文件、同一组键
    （账套 + 配方版本 + 单种，后缀 ``|menu``），配方版本变了自然作废。子菜单里选哪个候选是业务选择，由站点记，不记在这里。"""

    def __init__(self, store: ProbeStore, account_key: str, version: str):
        self.store, self.account_key, self.version = store, account_key, version

    def get(self, doc_type: str) -> Optional[List[str]]:
        found = self.store.get(self.account_key, self.version, f"{doc_type}|menu")
        path = (found or {}).get("path") if isinstance(found, dict) else None
        return [str(k) for k in path] if isinstance(path, list) and path else None

    def put(self, doc_type: str, path: List[str], title: str = "") -> None:
        self.store.put(self.account_key, self.version, f"{doc_type}|menu", {"path": [str(k) for k in path], "title": title})


def probe_form(engine: Engine, doc_type: str) -> Dict:
    """回 ``{"ok", "labels": [新建状态下看得到的格子标签], "missing": [配方要用、却没找到的]}``。不保存任何单。

    不按回车走格：对方格空着按回车会弹选择框；表头填法本来就按标签找格（``walk_header``），标签在就能录。"""
    engine.begin(None, doc_type)
    form = engine.form
    engine.ensure_form(doc_type)
    engine.command("new")
    engine.act([{"do": "wait", "ms": 1200}])
    try:
        fields = sorted(engine.field_map().values(), key=lambda f: (f["rect"][1], f["rect"][0]))
        labels: List[str] = list(dict.fromkeys(f["label"] for f in fields if f["label"]))
    finally:
        engine.command("cancel")
        engine.act([{"do": "wait", "ms": 900}])
        if any("1312" in w.get("title", "") for w in engine.windows):
            engine.act([{"do": "dialog_button", "text": "ตกลง"}, {"do": "wait", "ms": 900}])
    missing = [label for label in form.required_labels() if label not in labels]
    return {"ok": not missing, "labels": labels, "missing": missing}
