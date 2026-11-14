"""验收专用数据根（在测试机上以桌面用户跑）：从年度文件夹的登记表**只读**取出验收账套那一行，写成只有一行的登记表到本机盘。
CC 桌面版按数据根（含 secure\\SCCOMP.DBF 的文件夹）上报账套、不能只勾一本；把整个年度文件夹加成位置会连真实客户账套一起报成可写。
不写账套服务器上的任何文件。做法与顺序见验收测试标准 §5.1「验收专用数据根」。

    python make_root.py <年度文件夹的登记表> <验收账套完整路径> <本机数据根> [write]

不带 write 只看：登记表结构、命中的行、账套的税号与分店。末行打印一行 JSON。
"""
import json
import struct
import sys
from pathlib import Path


def table(raw: bytes):
    count, hlen, rlen = struct.unpack("<IHH", raw[4:12])
    fields, pos, off = [], 32, 1
    while raw[pos] != 0x0D:
        name = raw[pos:pos + 11].split(b"\0")[0].decode("ascii")
        fields.append((name, off, raw[pos + 16]))
        off += raw[pos + 16]
        pos += 32
    return count, hlen, rlen, fields


def one_row(raw: bytes, ledger: str) -> tuple[bytes, list]:
    """登记表原文 → (只留验收账套那一行、路径改成完整路径的登记表, 命中的行)。命中不是恰好一行、路径放不下就抛 ValueError。"""
    count, hlen, rlen, fields = table(raw)
    start, width = next((off, size) for name, off, size in fields if name == "PATH")
    tail = ledger.replace("/", "\\").rstrip("\\").rsplit("\\", 1)[-1].upper()
    hits = []
    for index in range(count):
        record = raw[hlen + index * rlen: hlen + (index + 1) * rlen]
        path = record[start:start + width].decode("cp874", "replace").strip()
        if record[0:1] != b"*" and path.replace("/", "\\").rstrip("\\").upper().rsplit("\\", 1)[-1] == tail:
            hits.append((index, path, record))
    if len(hits) != 1:
        raise ValueError(f"登记表里叫 {tail} 的未删除行有 {len(hits)} 行，要恰好一行")
    if len(ledger) > width:
        raise ValueError(f"路径 {len(ledger)} 字符，登记表的路径栏只有 {width}")
    record = bytearray(hits[0][2])
    record[start:start + width] = ledger.encode("ascii").ljust(width)
    head = bytearray(raw[:hlen])
    head[4:8] = struct.pack("<I", 1)
    head[28] = 0  # 不带索引 / 备注文件的标记：这份登记表只有它自己
    return bytes(head) + bytes(record) + b"\x1a", [(index, path) for index, path, _ in hits]


def identity(ledger: Path) -> dict:
    raw = (ledger / "ISINFO.DBF").read_bytes()
    _count, hlen, rlen, fields = table(raw)
    record = raw[hlen:hlen + rlen]
    return {name: record[off:off + size].decode("cp874", "replace").strip() for name, off, size in fields if name in ("TAXID", "ORGNUM")}


def main(argv) -> int:
    registry, ledger, root = Path(argv[0]), argv[1], Path(argv[2])
    built, hits = one_row(registry.read_bytes(), ledger)
    out = {"hits": hits, "identity": identity(Path(ledger))}
    if len(argv) > 3 and argv[3] == "write":
        target = root / "secure" / "SCCOMP.DBF"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(built)
        out.update(written=str(target), size=target.stat().st_size)
    print(json.dumps(out, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    import base64

    sys.exit(main([base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv[1:]]))
