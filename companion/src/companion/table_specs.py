# -*- coding: utf-8 -*-
"""合成测试建表用的最小字段规格 —— 真机写用客户真表,生产代码不读这些 spec。

**列宽照真表写**:这里曾把 STMAS.STKDES 建成 C(40),而真账套是 C(50),于是所有截断/比对
口径的缺陷在合成表上一律照不出来(两边都截到 40 就永远相等)。规格与真表漂移一次,测试就
从"验生产行为"退化成"验我们自己的想象"。tests/test_real_account_stock.py 有一条漂移闸。

余额表/期间表按**列必须齐**造,不适用"只建写入器要碰的子集":少一列就等于少一整类缺陷的
测试面。已经踩过三次 —— APBAL 缺 HP 组(现购记错桶测不出)、ARBAL 缺 NY 组、整张 ISPRD
从来没造过(于是"月桶列号是财年期号不是日历月""CY/NY 两个年槽"在合成栈上一次都碰不到,
而真账里 4 个账套非 1 月起账、1 月起账的账套 CY 也常常不是当前公历年)。
"""

from __future__ import annotations


def _months(prefix: str) -> str:
    """余额表一组双年月桶:`{P}1..12`(CY 槽)+ `{P}1NY..12NY`(NY 槽)。真表每组恒 24 列。"""
    cy = "; ".join(f"{prefix}{m} B" for m in range(1, 13))
    ny = "; ".join(f"{prefix}{m}NY B" for m in range(1, 13))
    return f"{cy}; {ny}"


def _isprd_slot(slot: str) -> str:
    """ISPRD 一个年槽的 36 列:BEG/END/LOCK × 12 期。真表恒 72 列(CY 12 + NY 12)。"""
    return "; ".join(
        f"BEG{m}{slot} D; END{m}{slot} D; LOCK{m}{slot} C(1)" for m in range(1, 13)
    )


# 合成测试建表用的最小字段规格(真机写用真表,不读这些 spec)。
TABLE_SPECS = {
    "APTRN": (
        "RECTYP C(1); DOCNUM C(12); DOCDAT D; REFNUM C(15); VATPRD D; VATDAT D; "
        "SUPCOD C(10); VATRAT N(5,2); FLGVAT C(1); AMOUNT B; AFTDISC B; TOTAL B; "
        "NETVAL B; VATAMT B; NETAMT B; REMAMT B; PAYAMT B; CSHPAY B; DOCSTAT C(1); "
        "ORGNUM N(5,0); USERID C(8); DEPCOD C(4); DUEDAT D; BILNUM C(15); "
        "NXTSEQ C(3); SRV_VATTYP C(1); CMPLAPP C(1); CMPLDAT D; PVATPRORAT C(1); "
        # AMTRAT0:免税金额(真表有;S 盘副本 HP67012204 等,混合税率的采购单头同进项税记录也记)
        "AMTRAT0 B"
    ),
    "ISVAT": (
        "VATREC C(1); RECTYP C(1); VATPRD D; VATDAT D; DOCDAT D; DOCNUM C(12); "
        # AMTRAT0:免税金额(真表有,实录第 10 条:混合免税的现销 / 退货单免税部分记这一栏)
        "REFNUM C(15); DESCRP C(60); AMT01 B; VAT01 B; AMTRAT0 B; DOCSTAT C(1); "
        "TAXID C(15); ORGNUM N(5,0); PRENAM C(15)"
    ),
    "GLJNL": (
        "JNLTYP C(2); VOUDAT D; VOUCHER C(12); DESCRP C(50); TRNSTAT C(1); "
        "DOCSTAT C(1); CREBY C(8)"
    ),
    "GLJNLIT": (
        "VOUCHER C(12); SEQIT C(2); VOUDAT D; ACCNUM C(15); DESCRP C(50); "
        "TRNTYP C(1); AMOUNT N(13,2); DEPCOD C(4)"
    ),
    # 真表 122 列,按单别分桶:HP 现购 / RR 赊购 / CP 付款 / GR 退货 / PS 折让。写错组=月报
    # 错位(BANKREC 真账反证:Express 原生 HP 单精确落 HP 桶、RR 桶为 0)。
    "APBAL": "SUPCOD C(10); BEGBAL B; "
    + "; ".join(_months(g) for g in ("HP", "RR", "CP", "GR", "PS")),
    "ISRUN": "DOCTYP C(2); DOCCOD C(2); PREFIX C(2); DOCNUM C(10); JNLTYP C(2); ACCNUM01 C(15); ACCNUM02 C(15)",
    "ARTRN": (
        "RECTYP C(1); DOCNUM C(12); DOCDAT D; YOUREF C(30); VATPRD D; VATDAT D; "
        "FLGVAT C(1); CUSCOD C(10); VATRAT N(5,2); AMOUNT B; AFTDISC B; TOTAL B; "
        "NETVAL B; VATAMT B; NETAMT B; RCVAMT B; REMAMT B; CSHRCV B; IVCAMT B; "
        "DOCSTAT C(1); ORGNUM N(5,0); USERID C(8); DEPCOD C(4); DUEDAT D; "
        "BILNUM C(15); NXTSEQ C(3); SRV_VATTYP C(1); CMPLAPP C(1); CMPLDAT D; "
        # 退货单 SR(RECTYP 5):CNTYP 退法(3 = 退现金)、SONUM 挂的原单号(实录 SR0000001;真表宽度待实录核对)
        "CNTYP C(1); SONUM C(12); "
        # AMTRAT0:免税金额(真表有;实录第 10 条,混合免税的现销 / 退货单单头同销项税记录也记)
        "AMTRAT0 B"
    ),
    # 真表 122 列:HS 现销 / IV 赊销 / DR 借项 / SR 退货 / RE 收款。
    "ARBAL": "CUSCOD C(10); BEGBAL B; "
    + "; ".join(_months(g) for g in ("HS", "IV", "DR", "SR", "RE")),
    # 会计期间表(真表 72 列)。余额表月桶的列号就是这里的期号 —— 整张缺失时,「按财年期号
    # 落列」「CY/NY 年槽」两类缺陷在合成栈上一个都碰不到。
    "ISPRD": _isprd_slot("") + "; " + _isprd_slot("NY"),
    # 单据明细行(V1 安全明细写的列;真表 45 列,合成只建写入器/读回要碰的)
    "STCRD": (
        "STKCOD C(20); LOCCOD C(4); DOCNUM C(12); SEQNUM C(3); DOCDAT D; "
        # REFNUM=供应商票号。库存卡报表的单据号列就取它(见 dbf_schema.stcrd_detail_row),
        # 合成表缺这一列 → 「明细行带没带票号」在测试里根本问不出来。
        "REFNUM C(15); "
        "DEPCOD C(4); POSOPR C(1); PEOPLE C(10); TRNQTY B; TQUCOD C(2); "
        "TFACTOR B; UNITPR B; DISC C(10); DISCAMT B; TRNVAL B; NETVAL B; "
        "XTRNQTY B; XUNITPR B; XTRNVAL B; XSALVAL B; STKDES C(50); "
        "PHYBAL B; MREMBAL B; MREMVAL B; ACCNUMDR C(15); ACCNUMCR C(15); "
        # 退货单 SR 的明细行:RDOCNUM C(15) = 原单号左对齐、原单行号右对齐到第 15 位、中间补空格(原生写法:实录 SR0000001「HS0000039     1」,
        # 测试机 S 盘副本 1039 本与 T 账套真表都是 C(15)),RETSTK Y 货回仓、FLAG 2
        "RDOCNUM C(15); RETSTK C(1); FLAG C(1); "
        # VATCOD:商品 VAT 类型(真表有;实录第 10 条,免税行记 '0'、应税行空)
        "VATCOD C(1); "
        # 批次(真表有;实录第 9 条盘点 TK 的盘盈挂最近一次进货那一批:MLOTNUM = 日期 + POSOPR + RDOCNUM)
        "MLOTNUM C(24); LOTBAL B; LOTVAL B; LUNITPR B; "
        # 仓间调拨 RL 的明细写 \x00（真表有；实录 RL0000001–0000003）
        "REIMBURSE C(1)"
    ),
    # 库存单单头（领用 OU / 调整 JU / 盘点 TK；真表 23 列，T9HENG15 10-25 读出的宽度）
    "STTRN": (
        "DOCNUM C(12); DOCDAT D; POSOPR C(1); PEOPLE C(10); REMARK C(50); LOCCOD C(4); TRNVAL B; "
        "REFNUM C(15); NXTSEQ C(3); ACCNUMDR C(15); ACCNUMCR C(15); DEPCOD C(4); POSTGL C(1); "
        "DOCSTAT C(1); USERID C(8); CHGDAT D"
    ),
    # 主档(自动建档写的列;真表 36/39 列,合成只建建档器要碰的)
    "APMAS": (
        "SUPCOD C(10); SUPTYP C(2); PRENAM C(15); SUPNAM C(60); "
        "ADDR01 C(50); ADDR02 C(50); ADDR03 C(30); VATRAT N(5,2); "
        "FLGVAT C(1); ACCNUM C(15); TAXID C(15); ORGNUM N(5,0); USERID C(8); "
        "CREBY C(8); CREDAT D; CHGDAT D; STATUS C(1)"
    ),
    "ARMAS": (
        "CUSCOD C(10); CUSTYP C(2); PRENAM C(15); CUSNAM C(60); "
        "ADDR01 C(50); ADDR02 C(50); ADDR03 C(30); ACCNUM C(15); "
        "TABPR C(1); TAXID C(15); ORGNUM N(5,0); USERID C(8); CREBY C(8); "
        "CREDAT D; CHGDAT D; STATUS C(1); "
        # 客户余额:Express 录退货单 SR 退现金时也冲减它(实录 SR0000001 160.5 → 60.5)
        "BALANCE B"
    ),
    # 商品主档(ensure_item 克隆建档碰的列 + V2-b 卖库存扣减的余额列 + 负库存出货结转要的
    # 标准成本 STNPR;真表 70 列,合成只建用到的)。
    # 列宽照真表(STKDES C(50)):这里曾按 C(40) 建,与真表的 C(50) 差 10 字,长名截断口径的
    # 缺陷(重复建档 + 销售死锁)因此在合成表上永远复现不出来。
    "STMAS": (
        "STKCOD C(20); STKDES C(50); STKDES2 C(50); STKTYP C(1); STKGRP C(4); "
        "ACCCOD C(4); QUCOD C(2); CQUCOD C(2); CFACTOR B; PQUCOD C(2); PFACTOR B; "
        "SQUCOD C(2); SFACTOR B; VATCOD C(1); STKLEV C(2); STKCLASS C(1); "
        "NEGALLOW C(1); ISINV C(1); ISSAL C(1); ISPUR C(1); STNPR B; "
        "TOTBAL B; TOTVAL B; OPNBAL B; OPNVAL B; MREMBAL B; MREMVAL B; USERID C(8); "
        "CREBY C(8); CREDAT D; CHGDAT D; STATUS C(1)"
    ),
    # 科目映射(ACCCOD→点号账码)。ensure_item 按目标科目反查/新建 ACCCOD 用。列宽照真表 C(50)。
    "ISACC": (
        "ACCTYP C(1); ACCCOD C(4); ACCDES C(50); ACCDES2 C(50); METHOD C(1); "
        "ACCNUM01 C(15); ACCNUM02 C(15); ACCNUM03 C(15); USERID C(8); CHGDAT D"
    ),
    # 会计科目表。自动建库存档要据 GROUP(1资产/2负债/3权益/4收入/5费用)+ PARENT 科目树
    # 判「选中的存货科目是不是真存货科目」,不合格就 escalate(见 stock_template)。
    "GLACC": (
        "ACCNUM C(15); ACCNAM C(50); ACCNAM2 C(50); LEVEL N(1,0); PARENT C(15); "
        "GROUP C(1); ACCTYP C(1); USEDEP C(1); NATURE C(1); STATUS C(1)"
    ),
    # 各仓结存(真表 68 列,列宽照 6902ASC / T9ASC 真表)。Express 录单时逐行维护(2026-10-22 用 5 套真账只读核对):
    # LOCBAL = BEGBAL + ΣQTY{期}、QTY{期} / VAL{期} = 该期该仓 STCRD 的带符号 XTRNQTY / XTRNVAL,期号同 ISPRD
    # (CY 12 + NY 12 两个年槽),LPURDAT / LSELDAT / LMOVDAT = 最后进货 / 销货 / 变动日;LOCVAL 不维护(几乎恒 0)。
    "STLOC": (
        "STKCOD C(20); LOCCOD C(4); AREA C(8); STKCLASS C(1); LOCBAL B; UNITPR B; LOCVAL B; "
        "LOCREO B; LOCRES B; LPURDAT D; LSELDAT D; LMOVDAT D; TAKDAT D; MLOTNUM C(24); "
        "MREMBAL B; MREMVAL B; BEGBAL B; BEGVAL B; "
        + "; ".join(f"QTY{m} B" for m in range(1, 13)) + "; "
        + "; ".join(f"QTY{m}NY B" for m in range(1, 13)) + "; "
        + "; ".join(f"VAL{m} B" for m in range(1, 13)) + "; "
        + "; ".join(f"VAL{m}NY B" for m in range(1, 13)) + "; "
        "STATUS C(1); INACTDAT D"
    ),
    # 代码表。TABTYP='20' 段是单位码(TYPCOD)—— 主档 QUCOD 只能写这里面的码。
    "ISTAB": (
        "TABTYP C(2); TYPCOD C(4); SHORTNAM C(15); SHORTNAM2 C(15); "
        "TYPDES C(50); TYPDES2 C(50); STATUS C(1)"
    ),
}
