# -*- coding: utf-8 -*-
"""造两张假表给实测用（不含任何真实公司）：
- PROBE.DBF：3 行、无索引，云端 CDX 索引程序只对它跑 TAGS（证明程序能跑，又不碰账套）；
- SCCOMP.DBF：2 家假公司，结构同 Express 公司登记表（同 companion/tests/test_express_locate.py），只给桥上自检 ccprobe 用，不上云端。
用法：python make_dbf.py <输出目录>
"""
import sys
from pathlib import Path

import dbf


def main(out: str) -> int:
    d = Path(out)
    d.mkdir(parents=True, exist_ok=True)
    for name in ("PROBE.DBF", "SCCOMP.DBF"):
        (d / name).unlink(missing_ok=True)
    t = dbf.Table(str(d / "PROBE.DBF"), "NAME C(10); QTY N(5,0)", dbf_type="db3")
    t.open(dbf.READ_WRITE)
    for i in range(3):
        t.append((f"PROBE{i}", i))
    t.close()
    t = dbf.Table(str(d / "SCCOMP.DBF"), "COMPNAM C(40); COMPCOD C(10); PATH C(40); CANDEL C(1)", codepage="cp874", dbf_type="vfp")
    t.open(dbf.READ_WRITE)
    t.append(("PROBE COMPANY A", "PA", "PA", "N"))
    t.append(("PROBE COMPANY B", "PB", "PB", "N"))
    t.close()
    print("ok", sorted(p.name for p in d.iterdir()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
