"""Pure invoice category rules from deployed ec2e5c957e6b. No model or IO.

Only the deterministic recognition consumers are migrated; expense LLM,
purchase documents, display tags and statistics are outside this contract.
"""


from __future__ import annotations


import re


from typing import Any, Dict, List, Optional


_PRESET: tuple = (
    (
        "ซื้อสินค้า/วัตถุดิบ",
        ("สินค้าสำเร็จรูป", "วัตถุดิบ", "บรรจุภัณฑ์", "ค่าขนส่งซื้อเข้า", "วัสดุสิ้นเปลืองการผลิต"),
    ),
    (
        "ค่าเดินทางและขนส่ง",
        (
            "ค่าแท็กซี่/แกร็บ",
            "ค่าน้ำมันเชื้อเพลิง",
            "ค่าโดยสารสาธารณะ",
            "ค่าตั๋วเครื่องบิน",
            "ค่าที่พัก/โรงแรม",
            "ค่าทางด่วน/ที่จอดรถ",
            "ค่าขนส่ง/พัสดุ",
        ),
    ),
    ("ค่าสาธารณูปโภค", ("ค่าน้ำประปา", "ค่าไฟฟ้า", "ค่าโทรศัพท์/อินเทอร์เน็ต", "ค่าแก๊ส")),
    (
        "ค่าใช้จ่ายสำนักงาน",
        (
            "เครื่องเขียน/วัสดุสิ้นเปลือง",
            "อุปกรณ์สำนักงาน",
            "ค่าไปรษณีย์/เอกสาร",
            "ซอฟต์แวร์/บริการออนไลน์",
            "ของใช้/ทำความสะอาดสำนักงาน",
        ),
    ),
    ("ค่าอาหารและรับรอง", ("ค่าอาหาร/เครื่องดื่ม", "ค่ารับรองลูกค้า", "ค่าอาหารประชุม/จัดเลี้ยง")),
    (
        "ค่าเช่า",
        (
            "ค่าเช่าสำนักงาน/อาคาร",
            "ค่าเช่าพื้นที่/หน้าร้าน",
            "ค่าเช่าอุปกรณ์/เครื่องจักร",
            "ค่าเช่ายานพาหนะ",
            "ค่าเช่าคลังสินค้า",
        ),
    ),
    (
        "ค่าจ้างและบริการภายนอก",
        (
            "ค่าจ้างทำของ/รับเหมา",
            "ค่าบริการวิชาชีพ (บัญชี/กฎหมาย)",
            "ค่าที่ปรึกษา",
            "ค่าออกแบบ/พัฒนา",
            "ค่าธรรมเนียมแพลตฟอร์ม",
        ),
    ),
    (
        "ค่าการตลาดและโฆษณา",
        (
            "ค่าโฆษณาออนไลน์",
            "ค่าสื่อ/สิ่งพิมพ์",
            "ค่าส่งเสริมการขาย/โปรโมชัน",
            "ค่าออกบูธ/อีเวนต์",
            "ค่ารีวิว/อินฟลูเอนเซอร์",
        ),
    ),
    (
        "ค่าซ่อมแซมและบำรุงรักษา",
        (
            "ค่าซ่อมอุปกรณ์/เครื่องจักร",
            "ค่าซ่อม/บำรุงยานพาหนะ",
            "ค่าบำรุงรักษาอาคาร/สถานที่",
            "ค่าดูแลระบบ/ไอที",
        ),
    ),
    (
        "ค่าใช้จ่ายพนักงาน",
        (
            "เงินเดือน/ค่าแรง",
            "ค่าล่วงเวลา/โบนัส",
            "ค่าสวัสดิการ",
            "ค่าประกันสังคม",
            "ค่าฝึกอบรม/สัมมนา",
            "ค่าเครื่องแบบ",
        ),
    ),
    (
        "ค่าธรรมเนียมและการเงิน",
        (
            "ค่าธรรมเนียมธนาคาร",
            "ดอกเบี้ยจ่าย",
            "ค่าธรรมเนียมราชการ",
            "ค่าธรรมเนียมบัตร/รับชำระเงิน",
        ),
    ),
    ("ภาษีและอากร", ("ภาษีหัก ณ ที่จ่าย", "ภาษีป้าย/โรงเรือน", "อากรแสตมป์", "ภาษีอื่น ๆ")),
    (
        "ค่าประกันภัย",
        (
            "ประกันทรัพย์สิน/อัคคีภัย",
            "ประกันยานพาหนะ",
            "ประกันความรับผิด",
            "ประกันสุขภาพ/อุบัติเหตุ",
        ),
    ),
    (
        "ครุภัณฑ์และสินทรัพย์",
        (
            "คอมพิวเตอร์/อุปกรณ์ไอที",
            "เฟอร์นิเจอร์/เครื่องใช้สำนักงาน",
            "เครื่องจักร/อุปกรณ์",
            "ยานพาหนะ",
            "ตกแต่ง/ปรับปรุงสถานที่",
        ),
    ),
    ("ค่าสุขภาพและการแพทย์", ("ค่ายา/เวชภัณฑ์", "ค่ารักษาพยาบาล", "ค่าตรวจสุขภาพ")),
    (
        "ค่าใช้จ่ายอื่น ๆ",
        (
            "ค่าบริจาค/CSR",
            "ค่าสมาชิก/ค่าธรรมเนียมวิชาชีพ",
            "ค่าปรับ/ค่าเสียหาย",
            "ค่าใช้จ่ายเบ็ดเตล็ด",
        ),
    ),
)


_TARGETS = {
    "food_drink": (
        ("ค่าอาหารและรับรอง", "餐饮", "อาหาร", "food", "meal", "entertainment"),
        ("ค่าอาหาร/เครื่องดื่ม", "餐费", "饮料", "อาหาร", "เครื่องดื่ม", "food", "drink"),
    ),
    "goods": (
        ("ซื้อสินค้า/วัตถุดิบ", "采购", "进货", "商品", "货物", "วัตถุดิบ", "goods", "purchase"),
        ("สินค้าสำเร็จรูป", "成品", "商品", "merchandise", "finished goods"),
    ),
    "fuel": (
        ("ค่าเดินทางและขนส่ง", "交通", "เดินทาง", "transport", "travel"),
        ("ค่าน้ำมันเชื้อเพลิง", "燃油", "油费", "汽油", "加油", "น้ำมัน", "fuel", "petrol"),
    ),
    "taxi": (
        ("ค่าเดินทางและขนส่ง", "交通", "เดินทาง", "transport", "travel"),
        ("ค่าแท็กซี่/แกร็บ", "打车", "แท็กซี่", "grab", "taxi"),
    ),
    "hotel": (
        ("ค่าเดินทางและขนส่ง", "交通", "เดินทาง", "transport", "travel"),
        ("ค่าที่พัก/โรงแรม", "酒店", "ที่พัก", "โรงแรม", "hotel"),
    ),
    "parking_toll": (
        ("ค่าเดินทางและขนส่ง", "交通", "เดินทาง", "transport", "travel"),
        ("ค่าทางด่วน/ที่จอดรถ", "停车", "ทางด่วน", "ที่จอดรถ", "parking", "toll"),
    ),
    "shipping": (
        ("ค่าเดินทางและขนส่ง", "ขนส่ง", "transport", "logistics"),
        ("ค่าขนส่ง/พัสดุ", "快递", "พัสดุ", "ขนส่ง", "shipping", "parcel"),
    ),
    "electric": (("ค่าสาธารณูปโภค", "水电", "utility"), ("ค่าไฟฟ้า", "电费", "ไฟฟ้า")),
    "water": (("ค่าสาธารณูปโภค", "水电", "utility"), ("ค่าน้ำประปา", "水费", "น้ำประปา")),
    "phone_net": (
        ("ค่าสาธารณูปโภค", "通讯", "utility"),
        ("ค่าโทรศัพท์/อินเทอร์เน็ต", "电话", "网络", "internet", "phone"),
    ),
    "office_supplies": (
        ("ค่าใช้จ่ายสำนักงาน", "办公", "office"),
        ("เครื่องเขียน/วัสดุสิ้นเปลือง", "文具", "เครื่องเขียน", "stationery"),
    ),
    "office_equipment": (
        ("ค่าใช้จ่ายสำนักงาน", "办公", "office"),
        ("อุปกรณ์สำนักงาน", "办公用品", "office equipment"),
    ),
    "software": (
        ("ค่าใช้จ่ายสำนักงาน", "办公", "office"),
        ("ซอฟต์แวร์/บริการออนไลน์", "软件", "software", "online"),
    ),
    "cleaning": (
        ("ค่าใช้จ่ายสำนักงาน", "办公", "office"),
        ("ของใช้/ทำความสะอาดสำนักงาน", "清洁", "ทำความสะอาด", "cleaning"),
    ),
    "bank_fee": (
        ("ค่าธรรมเนียมและการเงิน", "财务", "finance", "bank"),
        ("ค่าธรรมเนียมธนาคาร", "银行手续费", "bank fee"),
    ),
    "rent": (("ค่าเช่า", "租金", "rent"), ("ค่าเช่าสำนักงาน/อาคาร", "办公室租金", "office rent")),
    "marketing": (
        ("ค่าการตลาดและโฆษณา", "营销", "marketing"),
        ("ค่าโฆษณาออนไลน์", "广告", "advertising"),
    ),
    "repair_vehicle": (
        ("ค่าซ่อมแซมและบำรุงรักษา", "维修", "repair"),
        ("ค่าซ่อม/บำรุงยานพาหนะ", "车辆维修", "vehicle"),
    ),
    "medicine": (
        ("ค่าสุขภาพและการแพทย์", "医疗", "health"),
        ("ค่ายา/เวชภัณฑ์", "药品", "ยา", "medicine"),
    ),
    "insurance_vehicle": (
        ("ค่าประกันภัย", "保险", "insurance"),
        ("ประกันยานพาหนะ", "车辆保险", "vehicle"),
    ),
}


_ITEM_RULES: tuple[tuple[str, str]] = (
    (
        r"กาแฟ|คอฟฟี่|อเมริกาโน|ลาเต้|คาปู|มอคค่า|espresso|americano|latte|cappuccino|coffee|"
        r"ชา|เครื่องดื่ม|น้ำดื่ม|น้ำเปล่า|น้ำแข็ง|เบเกอรี่|เค้ก|ขนม|snack|drink|beverage|"
        r"อาหาร|ข้าว|ก๋วยเตี๋ยว|ไก่|หมู|ปลา|เนื้อ|ผัด|ทอด|ต้ม|ยำ|แกง|meal|food|"
        r"สลัด|salad|ผัก|veggie|vegetable|สเต็ก|สเต๊ก|steak|เบอร์เกอร์|burger|พิซซ่า|pizza|"
        r"พาสต้า|pasta|สปาเกตตี|spaghetti|เฟรนช์ฟราย|fries|ทรัฟเฟิล|truffle|วาซาบิ|wasabi|"
        r"บุฟเฟ่ต์|บุฟเฟต์|buffet|ซีฟู้ด|ทะเล|seafood",
        "food_drink",
    ),
    (r"ไฮดีเซล|ดีเซล|เบนซิน|แก๊สโซฮอล|น้ำมันเชื้อเพลิง|gasohol|diesel|benzin|petrol|fuel", "fuel"),
    (
        r"\bgrab\b|แกร็บ|\bbolt\b|โบลท์|lineman|ไลน์แมน|แท็กซี่|\btaxi\b|วินมอเตอร์ไซค์|ตุ๊กตุ๊ก",
        "taxi",
    ),
    (r"ทางด่วน|ค่าจอด|ที่จอดรถ|parking|toll", "parking_toll"),
    (r"โรงแรม|ที่พัก|hotel|accommodation", "hotel"),
    (r"ไปรษณีย์|ems|flash express|kerry|j&t|dhl|พัสดุ|shipping|parcel|delivery fee", "shipping"),
    (r"การไฟฟ้า|กฟภ|กฟน|\bmea\b|\bpea\b|ค่าไฟฟ้า|ค่าไฟ", "electric"),
    (r"การประปา|กปน|กปภ|ค่าน้ำประปา", "water"),
    (
        r"\bais\b|เอไอเอส|ทรูมูฟ|\btrue\b|\bdtac\b|ดีแทค|3bb|อินเทอร์เน็ต|internet|ค่าโทรศัพท์",
        "phone_net",
    ),
    (r"กระดาษ|ปากกา|ดินสอ|สมุด|เครื่องเขียน|stationery|toner|หมึกพิมพ์", "office_supplies"),
    (r"คีย์บอร์ด|เมาส์|printer|เครื่องพิมพ์|อุปกรณ์สำนักงาน|office equipment", "office_equipment"),
    (r"software|saas|hosting|domain|cloud|ซอฟต์แวร์|บริการออนไลน์", "software"),
    (r"ทำความสะอาด|น้ำยาล้าง|ถุงขยะ|cleaning", "cleaning"),
    (r"ค่าธรรมเนียมธนาคาร|bank fee|ค่าธรรมเนียมโอน|transfer fee", "bank_fee"),
    (r"ค่าเช่า|rent", "rent"),
    (r"facebook ads|google ads|โฆษณา|advertising|marketing", "marketing"),
    (r"ซ่อมรถ|บำรุงรถ|ยางรถ|vehicle repair|car repair", "repair_vehicle"),
    (r"ยา|เวชภัณฑ์|medicine|pharmacy|drugstore", "medicine"),
    (r"ประกันรถ|vehicle insurance|car insurance", "insurance_vehicle"),
)


_VENDOR_RULES: tuple[tuple[str, str]] = (
    (r"cafe amazon|amazon coffee|กาแฟพันธุ์ไทย|starbucks|คาเฟ่|cafe|coffee", "food_drink"),
    (
        r"ร้านอาหาร|restaurant|little betong|foodstory|ภัตตาคาร|ครัว|kitchen|"
        r"บุฟเฟ่ต์|บุฟเฟต์|buffet|ซีฟู้ด|seafood|cafe|คาเฟ่",
        "food_drink",
    ),
    # 批发/大卖场 → 采购商品默认(品名清楚时 _ITEM_RULES 已先归位:笔→办公、食品→餐饮;
    # 只有品名识别不清才落到这里 = 调用方记 vendor_default)。
    (
        r"makro|แม็คโคร|แม็กโคร|\btops\b|ท็อปส์|lotus|โลตัส|tesco|เทสโก้|big ?c|บิ๊กซี|"
        r"gourmet market|villa market|วิลล่า มาร์เก็ต|foodland|ฟู้ดแลนด์",
        "goods",
    ),
    # 便利店 → 餐饮/便利店默认(同上:品名优先;不清才落此默认 = vendor_default)。
    (
        r"7-?eleven|seven ?eleven|เซเว่น|เซเว่นอีเลฟเว่น|7-?11|cp all|ซีพี ?ออลล์|"
        r"familymart|แฟมิลี่มาร์ท|family mart|มินิ ?บิ๊กซี",
        "food_drink",
    ),
    (r"\bgrab\b|แกร็บ|\bbolt\b|โบลท์|lineman|ไลน์แมน|แท็กซี่|\btaxi\b", "taxi"),
    (
        r"บางจาก|bangchak|ปตท|\bptt\b|เชลล์|\bshell\b|คาลเท็กซ์|caltex|เอสโซ่|\besso\b|ซัสโก้|susco",
        "fuel",
    ),
    (r"การไฟฟ้า|กฟภ|กฟน|\bmea\b|\bpea\b", "electric"),
    (r"การประปา|กปน|กปภ", "water"),
    (r"\bais\b|เอไอเอส|ทรูมูฟ|\btrue\b|\bdtac\b|ดีแทค|3bb", "phone_net"),
    (r"ไปรษณีย์|flash express|kerry|j&t|dhl", "shipping"),
    (r"ธนาคาร|bank", "bank_fee"),
)


def _norm_name(name: str) -> str:
    return re.sub(r"[\s\-/()（）&·]+", "", (name or "").casefold())


def _alias_match(name: str, aliases: tuple[str, ...]) -> bool:
    n = _norm_name(name)
    return bool(n) and any((a := _norm_name(alias)) and (a in n or n in a) for alias in aliases)


def _resolve_target(categories: list, target: str):
    parent_aliases, child_aliases = _TARGETS[target]
    parent_hit = None
    for parent in categories or []:
        if _alias_match(parent.get("name"), parent_aliases):
            parent_hit = parent
            for child in parent.get("children") or []:
                if _alias_match(child.get("name"), child_aliases):
                    return parent["id"], child["id"]
    for parent in categories or []:
        for child in parent.get("children") or []:
            if _alias_match(child.get("name"), child_aliases):
                return parent["id"], child["id"]
    return (parent_hit["id"], None) if parent_hit else (None, None)


def _first_rule(text: str, rules: tuple[tuple[str, str]], categories: list):
    for pattern, target in rules:
        if re.search(pattern, text or "", re.IGNORECASE):
            cid, sid = _resolve_target(categories, target)
            if cid:
                return cid, sid
    return None, None


def classify_rules(vendor: str, descs: str, categories: list):
    """确定性归类,返回 (cat_id, sub_id, layer)。layer ∈ 'item'|'vendor'|''。

    品名优先(item)→ 商户(vendor)→ 商户+品名合并再过品名。品名优先避免 PTT/Cafe Amazon
    咖啡误分燃油;7-11/Makro 等便利店/大卖场只在品名识别不清时落到商户默认(layer='vendor',
    调用方据此记 category_source=vendor_default 便于观察)。
    """
    cid, sid = _first_rule(descs or "", _ITEM_RULES, categories)
    if cid:
        return cid, sid, "item"
    cid, sid = _first_rule(vendor or "", _VENDOR_RULES, categories)
    if cid:
        return cid, sid, "vendor"
    cid, sid = _first_rule(f"{vendor or ''} {descs or ''}", _ITEM_RULES, categories)
    return (cid, sid, "item") if cid else (None, None, "")


def match_user_category(text: str, categories: list):
    """用户改/教分类用词(跨语言)→ 科目树最接近真叶子 (cat_id, sub_id);无把握 → (None, None)。

    复用 _TARGETS 跨语言别名(商品/goods→ซื้อสินค้า·油费/fuel→ค่าน้ำมันเชื้อเพลิง·餐饮→ค่าอาหาร):
    先子类别名(更具体)再大类别名匹配用户词,命中即解析到本套账真叶子。改分类专用(记账走规则/LLM)。
    短词(<2 字)不猜,避免单字误命中。
    """
    if len((t := (text or "").strip())) < 2:
        return None, None
    for use_child in (True, False):
        for target, (parent_aliases, child_aliases) in _TARGETS.items():
            if _alias_match(t, child_aliases if use_child else parent_aliases):
                cid, sid = _resolve_target(categories, target)
                if cid:
                    return cid, sid
    return None, None


def _category_names(cats: list, cat_id, sub_id) -> tuple[str, str]:
    for p in cats:
        if p["id"] == cat_id:
            sub_name = next((c["name"] for c in (p.get("children") or []) if c["id"] == sub_id), "")
            return p["name"], sub_name
    return "", ""


def item_descriptions(fields: Dict[str, Any]) -> str:
    """票面品名拼成一段文本(喂关键词规则的 descs)。无 items → 空串(仍可靠卖方名匹配)。"""
    items = fields.get("items") if isinstance(fields, dict) else None
    if not isinstance(items, list):
        return ""
    return " ".join(
        str(it.get("name") or it.get("description") or "") for it in items if isinstance(it, dict)
    )


def _name(tree: List[Dict[str, Any]], cid: Any, sid: Any) -> Optional[str]:
    """(大类id, 子类id) → 树里的名字(子类优先,无子则大类名);复用 image_category._category_names。"""
    cat_name, sub_name = _category_names(tree, cid, sid)
    return sub_name or cat_name or None


def resolve_tag(fields: Dict[str, Any], tree: List[Dict[str, Any]]) -> Optional[str]:
    """归到本套账分类 → 返回该分类名(泰语·子类优先);不中 → None(留空)。"""
    if not (isinstance(fields, dict) and tree):
        return None
    vendor = str(fields.get("seller_name") or "")
    descs = item_descriptions(fields)
    cid, sid, _layer = classify_rules(vendor, descs, tree)
    if not cid:
        cid, sid = match_user_category(str(fields.get("category") or ""), tree)
    if not cid:
        return None
    return _name(tree, cid, sid)


def sanitize_learned(word: Optional[str], tree: List[Dict[str, Any]]) -> Optional[str]:
    """净化"同卖方学习"到的旧科目值:是本套账真实分类名(预置/自定义)→ 原样保留;否则(旧
    模型自由文本,如中文'化妆品')→ 跨语言映射到本套账分类,映射不上 → None(不回灌非本套账中文)。
    无树(无套账/载树失败)→ 原样保留(回落·不误伤)。调用方仅在返回真值时才覆盖,None 不清空。
    """
    if not word:
        return None
    if not tree:
        return word
    w = str(word).strip()
    for parent in tree:
        if parent.get("name") == w:
            return w
        for child in parent.get("children") or []:
            if child.get("name") == w:
                return w
    cid, sid = match_user_category(w, tree)
    return _name(tree, cid, sid) if cid else None
