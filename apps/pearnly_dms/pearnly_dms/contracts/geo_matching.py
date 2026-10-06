"""Pure address matching shared by live writes and cached previews."""
import html
import unicodedata
from dataclasses import replace

PREFIXES = ("จังหวัด", "อำเภอ", "ตำบล", "แขวง", "เขต", "จ.", "อ.", "ต.")


def normalized(name):
    value = " ".join(unicodedata.normalize("NFKC", html.unescape(str(name or ""))).split())
    for prefix in PREFIXES:
        prefix = unicodedata.normalize("NFKC", prefix)
        if value.startswith(prefix):
            return value[len(prefix):].strip()
    return value


def unique(options, name):
    target = normalized(name)
    matches = {str(value) for value, label in options if value and normalized(label) == target} if target else set()
    return next(iter(matches)) if len(matches) == 1 else ""


def _one_edit_apart(a, b):
    """恰好差一个字（替换、多一个或少一个）。"""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def near(options, name):
    """身份证照片读错**一个字**时的唯一候选（2026-10-06 实例：ต.หนองขมาร 读成 หนองชมาร）。

    只在同一父级的候选里找；名字太短（不足 4 个字）、没有或有不止一个只差一个字的候选，
    一律留空交给用户在编辑页选 —— 不猜。精确匹配永远优先（见 `resolve`）。
    """
    target = normalized(name)
    if len(target) < 4:
        return ""
    hits = {str(value) for value, label in options
            if value and _one_edit_apart(normalized(label), target)}
    return next(iter(hits)) if len(hits) == 1 else ""


def resolve(address, provinces, children):
    """Never guess the first option; unresolved parents leave all children empty.

    邮编那一级有一条**有意的例外**（迁移自线上基准
    `services/erp/mrerp_dms_client_forms.py` 的同名注释）：泰国身份证正面常常不印邮编，
    而"这条街在 DMS 只对应一个邮编"时补上它是**确定性的**、不是猜。少了这条例外，
    按身份证建的客户会因为邮编为空被写入守卫拦下 —— 实测里操作员因此被逼进编辑页
    手动挑邮编，而线上本来是"拍完身份证直接录入"。
    多个候选仍然留空，交给用户选（绝不取第一项）。

    府、县、镇三级在没有精确匹配时，接受同一父级里**唯一**只差一个字的名字（`near`）：
    识别把一个字母读错，整条地址就断在那一级，销售被迫进编辑页。邮编不做近似。
    """
    address = replace(address, province_id="", district_id="", subdistrict_id="", zipcode_id="")
    parent = ""
    # 本街道的邮编候选：**复用**这一轮已经取回的那一份，不为了补邮编再发一次请求。
    zipcode_rows = ()
    for level, id_field, name_field in (
        ("provinces", "province_id", "province_name"),
        ("districts", "district_id", "district_name"),
        ("subdistricts", "subdistrict_id", "subdistrict_name"),
        ("zipcodes", "zipcode_id", "zipcode"),
    ):
        rows = provinces if level == "provinces" else children(level, parent)
        if level == "zipcodes":
            zipcode_rows = rows
        name = getattr(address, name_field)
        parent = unique(rows, name) or ("" if level == "zipcodes" else near(rows, name))
        if not parent:
            break
        address = replace(address, **{id_field: parent})
    if address.subdistrict_id and not address.zipcode_id:
        candidates = {str(value) for value, _label in zipcode_rows if value}
        if len(candidates) == 1:
            address = replace(address, zipcode_id=next(iter(candidates)))
    return address


def complete_tree(geo):
    """Shape and parent coverage of a whole native address tree (no IO)."""
    if not isinstance(geo, dict) or set(geo) != {"provinces", "districts", "subdistricts", "zipcodes"}:
        return False
    parents = {""}
    for level in ("provinces", "districts", "subdistricts", "zipcodes"):
        mapping = geo[level]
        if not isinstance(mapping, dict) or set(mapping) != parents:
            return False
        children = set()
        for rows in mapping.values():
            if not isinstance(rows, list):
                return False
            ids = []
            for row in rows:
                if (not isinstance(row, (list, tuple)) or len(row) != 2
                        or not str(row[0] or "").strip() or not str(row[1] or "").strip()):
                    return False
                ids.append(str(row[0]))
            if len(ids) != len(set(ids)) or (level != "zipcodes" and children.intersection(ids)):
                return False
            children.update(ids)
        if level == "provinces" and not children:
            return False
        parents = children
    return True
