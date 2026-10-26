"""只读解析 Express 的 DBF（VFP 格式，cp874）。只用标准库，测试机（Windows）和本机（离线自检）共用。

只读原始字节，不加锁、不写回；调用方先把表拷到临时目录再读（见 agent_win.py 的 book_rows）。
"""
from __future__ import annotations

import datetime as dt
import struct
from typing import Dict, Iterator, List, Optional, Tuple

ENCODING = "cp874"


def fields(data: bytes) -> Tuple[int, int, int, List[Tuple[str, str, int, int]]]:
    """返回 (行数, 表头长, 行长, [(字段名, 类型, 长度, 小数位)])。"""
    count, header_len, row_len = struct.unpack("<IHH", data[4:12])
    out = []
    pos = 32
    while pos < header_len and data[pos] != 0x0D:
        name = data[pos:pos + 11].split(b"\0")[0].decode("latin1")
        out.append((name, chr(data[pos + 11]), data[pos + 16], data[pos + 17]))
        pos += 32
    return count, header_len, row_len, out


def _value(kind: str, raw: bytes, decimals: int):
    if kind == "B" and len(raw) == 8:
        return struct.unpack("<d", raw)[0]
    if kind == "I" and len(raw) == 4:
        return struct.unpack("<i", raw)[0]
    if kind == "Y" and len(raw) == 8:
        return struct.unpack("<q", raw)[0] / 10000
    if kind in ("N", "F"):
        text = raw.decode("latin1").strip()
        if not text:
            return None
        try:
            return float(text) if (decimals or "." in text) else int(text)
        except ValueError:
            return None
    if kind == "D":
        text = raw.decode("latin1").strip()
        if len(text) != 8 or not text.isdigit():
            return None
        try:
            return dt.date(int(text[:4]), int(text[4:6]), int(text[6:]))
        except ValueError:
            return None
    if kind == "L":
        return raw[:1] in (b"T", b"t", b"Y", b"y")
    if kind == "T" and len(raw) == 8:
        jd, ms = struct.unpack("<ii", raw)
        if jd <= 0:
            return None
        day = dt.date.fromordinal(jd - 1721425)
        return dt.datetime.combine(day, dt.time()) + dt.timedelta(milliseconds=ms)
    if kind in ("M", "G", "W") and len(raw) == 4:
        return None  # 备注指针，这里用不到
    return raw.decode(ENCODING, "replace").rstrip(" \0")


def rows(data: bytes, since: int = 0, keep: Optional[set] = None) -> Iterator[Dict]:
    """逐行读出（含删除标记 ``_del`` 和行号 ``_row``）。``since``：从第几行开始；``keep``：只留这些字段。"""
    count, header_len, row_len, specs = fields(data)
    for index in range(max(since, 0), count):
        start = header_len + index * row_len
        raw = data[start:start + row_len]
        if len(raw) < row_len:
            break
        value = {"_row": index, "_del": raw[:1] == b"*"}
        pos = 1
        for name, kind, length, decimals in specs:
            if keep is None or name in keep:
                value[name] = _value(kind, raw[pos:pos + length], decimals)
            pos += length
        yield value


def count(data: bytes) -> int:
    return struct.unpack("<I", data[4:8])[0]


def write_table(specs: List[Tuple[str, str, int, int]], records: List[Dict], deleted: Optional[set] = None) -> bytes:
    """离线自检用：按给定字段写一张最小的 VFP 表（只支持 C / N / D / B / L）。不用于真账套。"""
    deleted = deleted or set()
    header_len = 32 + 32 * len(specs) + 1
    row_len = 1 + sum(length for _, _, length, _ in specs)
    head = bytearray(32)
    head[0] = 0x30
    head[4:12] = struct.pack("<IHH", len(records), header_len, row_len)
    body = bytearray()
    for name, kind, length, decimals in specs:
        spec = bytearray(32)
        spec[0:len(name)] = name.encode("latin1")
        spec[11] = ord(kind)
        spec[16] = length
        spec[17] = decimals
        body += spec
    out = bytearray(head + body + b"\x0d")
    for index, record in enumerate(records):
        out += b"*" if index in deleted else b" "
        for name, kind, length, decimals in specs:
            value = record.get(name)
            if kind == "B":
                out += struct.pack("<d", float(value or 0))
            elif kind == "N":
                text = "" if value is None else (f"{float(value):.{decimals}f}" if decimals else str(int(value)))
                out += text.rjust(length).encode("latin1")[:length]
            elif kind == "D":
                out += (value.strftime("%Y%m%d") if value else "").ljust(8).encode("latin1")
            elif kind == "L":
                out += b"T" if value else b"F"
            else:
                out += str(value or "").encode(ENCODING).ljust(length, b" ")[:length]
    out += b"\x1a"
    return bytes(out)
