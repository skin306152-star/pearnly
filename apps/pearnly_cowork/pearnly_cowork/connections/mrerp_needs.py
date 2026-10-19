"""MR.ERP 推之前每份账套要选一次的（导入菜单、主档默认值）：按票面方向看哪几项会用到、此刻还要不要人选。纯函数，不碰框架与 IO。

同网页推送前那一步（`public/desk/targets.js::mrerp_menu_ready`）：菜单与默认值的规则在 `targets.mrerp_menus` /
`targets.mrerp_masters`，视图由 `connections.service.mrerp_menu` 给；这里只按方向挑出会用到的、要人选的，给 CC 工具
（`assistant.tools`：推之前先问、`cowork_push_choices`、`cowork_mrerp_setup`、定时任务保存前）用。
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from pearnly_cowork.targets import mrerp_masters as master_rules, mrerp_menus as menu_rules


def used(direction: Optional[str] = None) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """推这个方向的票会用到的（单据类型, 主档字段）；方向没定两边都算。"""
    kinds = menu_rules.KINDS_FOR_DIRECTION.get(direction) or menu_rules.KINDS
    fields = master_rules.FIELDS_FOR_DIRECTION.get(direction) or tuple(
        dict.fromkeys(field for group in master_rules.FIELDS_FOR_DIRECTION.values() for field in group))
    return tuple(kinds), tuple(fields)


def needed(view: Any, direction: Optional[str] = None) -> Dict[str, Any]:
    """菜单视图（`connections.service.mrerp_menu`）里这张票推之前还要人选的：``{"known", "menus": [单据类型],
    "defaults": [主档字段]}``，只看这个方向会用到的。``known`` 为假：菜单或主档还没从 MR.ERP 读到过，这时说不出要不要选
    （推送时 MR.ERP 那边按读到的再核对）。"""
    kinds, fields = used(direction)
    view = view if isinstance(view, dict) else {}
    defaults = view.get("defaults") if isinstance(view.get("defaults"), dict) else {}
    return {"known": bool(view.get("known") and defaults.get("known")),
            "menus": [kind for kind in kinds if ((view.get("kinds") or {}).get(kind) or {}).get("needed")],
            "defaults": [field for field in fields if ((defaults.get("fields") or {}).get(field) or {}).get("needed")]}


__all__ = ["needed", "used"]
