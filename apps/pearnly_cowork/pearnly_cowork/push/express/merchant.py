"""Known numeric merchant names used by the source field cleaner."""
import re

_BRAND_ALIASES = (
    (r"7-?eleven|seven ?eleven|เซเว่น|7-?11|cp all|ซีพี ?ออลล์", "7-eleven"),
    (r"familymart|family mart|แฟมิลี่มาร์ท", "familymart"),
    (r"makro|แม็คโคร|แม็กโคร", "makro"),
    (r"\btops\b|ท็อปส์", "tops"),
    (r"lotus|โลตัส|tesco|เทสโก้", "lotus"),
    (r"big ?c|บิ๊กซี", "bigc"),
    (r"cafe amazon|amazon coffee|กาแฟอเมซอน|กาแฟพันธุ์ไทย", "cafe-amazon"),
    (r"starbucks|สตาร์บัคส์", "starbucks"),
    (r"bangchak|บางจาก", "bangchak"),
    (r"\bptt\b|ปตท", "ptt"),
    (r"\bshell\b|เชลล์", "shell"),
    (r"caltex|คาลเท็กซ์", "caltex"),
    (r"\besso\b|เอสโซ่", "esso"),
)


def is_known_brand(name: str) -> bool:
    """名字归一命中已知大连锁(含纯数字店号 711/7-11)→ True。供 field_clean 守卫:像金额的纯数字
    若实为店号则当店名保留、不当金额清空。"""
    return any(re.search(pattern, name or "", re.IGNORECASE) for pattern, _key in _BRAND_ALIASES)
