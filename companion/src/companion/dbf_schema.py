"""Express 账套 DBF 表结构 + 进项(RR/HP)写入字段映射。

字段/类型对真 DATAT(`\\accserver\\ACCOUNT\\70EXP\\test`)逆向得来(见 08 施工单 + 标准答案
RR580101-001/PTT)。这里只声明**写入器要碰的列**(非全表 58 列):合成测试据此建 VFP 表,
真机写直接用同名列追加到真表。

要点:
- 金额列是 VFP `B`(8 字节 double);唯 `GLJNLIT.AMOUNT` 是 `N(13,2)`。
- 日期列存**公历** date;佛历只出现在 DOCNUM 字符串(YYMMDD)。
- codepage cp874(泰文);建表须 dbf_type='vfp'(否则不认 B 类型)。
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path

import dbf

from .table_specs import TABLE_SPECS as _TABLE_SPECS

CODEPAGE = "cp874"
# 打开账套表一律按「遇到解不出的字节就替换」读:真实账套里有个别行带 cp874 解不出的字节(测试机 62 套里 3 套,
# 0x90 / 0x99),严格模式下读到那一行就抛错,整张单回滚(2026-10-08 回归测出)。只影响读出来的文本,文件里原有的
# 字节不动(改一行时只写被改的字段);坏行的码对不上任何东西,等于跳过。
UNICODE_ERRORS = "replace"
DBF_TYPE = "vfp"


def live_rows(table):
    """逐行 yield 表里**还算数**的记录(跳过软删行)。

    DBF 的删除是打标记不是抹数据:会计在 Express 里删掉一张单据,那几行仍躺在 .DBF 里,
    直到有人跑 PACK 才真消失。所以凡是问「这条业务记录还在不在」的查询——幂等查重、找已有
    客户/商品码复用、写后读回校验——都必须过这一层,否则会在一条 Express 里根本看不见的
    幽灵行上命中(2026-07-25 现场:客户删了 IV690531-001 再重推,幂等闸认下那条软删行,
    每次都报「已存在,跳过」,发票永远进不去)。

    反过来,「下一个可用编号是多少」必须把软删行也算上(见 _next_docnum / _next_code /
    _next_stkcod)——跳过它们就会把已删单据的号再发一次,新旧同号撞在一起。
    """
    for r in table:
        if not dbf.is_deleted(r):
            yield r


def orgnum_to_branch5(v) -> str:
    """ARMAS/APMAS 的 ORGNUM 值 → 5 位分店号串('18'/18/'18.0'→'00018';空/非数字/越界→'00000' 总店)。

    唯一实现:匹配(master_create)与目录上报(catalog_probe)必须对同一个 ORGNUM 给出
    同一个分店号,各写一份迟早漂移。走数值转换而非剥字符:dbf 数字列可能带小数形态,
    '18.0' 剥字符会错成 '180'。
    """
    try:
        n = int(float(str(v).strip() or 0))
    except (TypeError, ValueError):
        n = 0
    if n < 0 or n > 99999:
        n = 0
    return f"{n:05d}"


# RECTYP:赊购 RR=3(未付)/ 现购 HP=1(已付)。
RECTYP = {"RR": "3", "HP": "1"}

# GLJNLIT.SEQIT 按分录角色定(标准答案全表一致:采购=0 / 进项税=3 / 应付=6)。
SEQIT = {"purchase": "0", "input_vat": "3", "ap": "6"}

JNLTYP_PURCHASE = "04"  # 采购日记账
ISVAT_VATREC_INPUT = "P"  # 进项税(喂 ภ.พ.30)
TRNTYP_DEBIT = "0"
TRNTYP_CREDIT = "1"

# ── 销项(镜像采购·见 09/10)──────────────────────────────────
# RECTYP 按单别分:赊销 IV=3 / 现销 HS=1(镜像采购 RR=3/HP=1)。
# ⚠️ 旧版误设全表 '1'(参照的 DATAT 只有现销·零赊销)→ 报表筛 RECTYP=3 看不到赊销单。
# 逐列比对 57ASCRD 254 张真 IV 锁死:赊销=3。
SALES_RECTYP = {"IV": "3", "HS": "1"}
SALES_DOCTYPES = ("IV", "HS")  # IV=ขายเงินเชื่อ 赊销 / HS=ขายเงินสด 现销
JNLTYP_SALES = "03"  # 销售日记账
ISVAT_VATREC_OUTPUT = "S"  # 销项税(喂 ภ.พ.30 销项侧)
# 销项 GLJNLIT.SEQIT(逆向 gold HL580301-001):借方资产=2 / 贷收入=5 / 贷销项税=6。
SALES_SEQIT = {"asset": "2", "revenue": "5", "output_vat": "6"}
SALES_WRITE_TABLES = ("ISRUN", "ARTRN", "ISVAT", "GLJNL", "GLJNLIT", "ARBAL")

# 有明细行时额外要重建索引的主档(销项/采购共用):明细表 + 商品主档 + 科目映射。
# 新建对手方(ARMAS/APMAS)由写入方按方向另行追加。
MASTER_EXTRA_TABLES = ("STCRD", "STMAS", "ISACC")

# ── V1 安全明细(STCRD 单据明细行 · 直接科目行 · 不碰库存/成本)──────────────
# 逆向 gold IV0000004(Express 亲手建·服务行 42-15-00-00):明细落 STCRD,按 DOCNUM 挂单头,
# ARTRN.NXTSEQ 必须=明细行数(否则 Express 视作 0 行,清掉孤儿明细)。**无成本签名**:
# XUNITPR=0 / XTRNVAL=0 / MREM*/PHYBAL/LOT*=0 → Express 不推 COGS、不动库存余额。
# STKCOD 放收入/采购**科目码**(直接科目行,非真实存货码)→ 贷/借该科目,零库存影响。
STCRD_POSOPR = "9"  # gold 实写值(过账操作位)· 销货 IV 行
# 进货入库 / 采购明细行:Express 原生写 "0"。真账 69SINCER(2026-09-28 压测回读):RR 行 2247/2247 为 "0"、
# IV 行 2645/2645 为 "9"。此前采购也写 "9"(只照一张销货样本定的)。期初行未核实,照旧 "9"。
STCRD_POSOPR_PURCHASE = "0"
STCRD_DEFAULT_LOCCOD = "01"
# 小助手写入的录入人(USERID / CREBY,C(8))。以前写 BIT9 —— 那是测试机所在事务所的真人账号,别的事务所没有这个人,
# 在本所又把推送记到一个真人名下。Express 打开账套不拿单据上的用户名去比对用户表(真实账套里大多数单据的录入人
# 都不在本机用户表,照常打开;2026-10-08 核对),所以写一个固定的、看得出是 Pearnly 写的名字。
PEARNLY_USERID = "PEARNLY"

# OCR 自由文本单位 → Express 2 字符单位码(只作 unit_code.resolve 的后备,结果仍须是该账套的合法码)。
# 2026-10-08 按测试机 62 套真实账套改正:「ชิ้น」是 ชน(62/62,旧表写 ชิ 一套都没有)、「อัน」是 อน(62/62)。
_UNIT_MAP = {
    "ลิตร": "ลต",
    "ลต": "ลต",
    "l": "ลต",
    "liter": "ลต",
    "litre": "ลต",
    "ขวด": "ขด",
    "bottle": "ขด",
    "ถัง": "ถง",
    "ถง": "ถง",
    "ชิ้น": "ชน",
    "pcs": "ชน",
    "piece": "ชน",
    "pc": "ชน",
    "อัน": "อน",
    "กล่อง": "กล",
    "box": "กล",
    "แกลลอน": "กน",
    "gallon": "กน",
    "ชุด": "ชด",
    "set": "ชด",
    "กระป๋อง": "กป",
    "can": "กป",
}


def unit_to_tqucod(unit: str) -> str:
    """OCR 单位 → Express TQUCOD(2 字符)。未识别返回 ''(留空,不乱填无效码)。"""
    return _UNIT_MAP.get((unit or "").strip().lower(), "")


def stcrd_detail_row(
    docnum: str,
    seqnum: int,
    docdat,
    account_code: str,
    party_code: str,
    name: str,
    qty: float,
    unit_price: float,
    amount: float,
    *,
    unit: str = "",
    loccod: str = STCRD_DEFAULT_LOCCOD,
    depcod: str = "",
    ref_no: str = "",
    posopr: str = STCRD_POSOPR,
) -> dict:
    """一条 STCRD 直接科目明细行(gold IV0000004 模板·无成本/无库存)。amount=行净额。

    `ref_no` = 单头 REFNUM(供应商票号)。真账镜像 17 套:采购行 RR 18168/18533、HP
    147/147 带它,销项行 IV 0/9266、HS 0/5492 一律留空 —— 所以采购侧传、销项侧不传,
    这是照抄 Express 自己的写法,不是"都填上更整齐"。Express 随程序发的库存卡报表按
    `IIF(STCRD->REFNUM # '  ', STCRD->REFNUM, STCRD->DOCNUM-'-'-STCRD->SEQNUM)` 取
    单据号列:留空 = 那一行在库存卡上只剩内部单号,对不上供应商票。
    """
    return {
        "STKCOD": account_code,
        "LOCCOD": loccod,
        "DOCNUM": docnum,
        "SEQNUM": str(seqnum),
        "DOCDAT": docdat,
        "REFNUM": (ref_no or "")[:15],
        "DEPCOD": depcod,
        "POSOPR": posopr,
        "PEOPLE": party_code,
        "TRNQTY": float(qty),
        "TQUCOD": unit_to_tqucod(unit),
        "TFACTOR": 1.0,
        "UNITPR": float(unit_price),
        "DISCAMT": 0.0,
        "TRNVAL": float(amount),
        "NETVAL": float(amount),
        "XTRNQTY": float(qty),
        "XUNITPR": 0.0,  # 无成本 → 无 COGS
        "XTRNVAL": 0.0,
        "XSALVAL": float(amount),
        "STKDES": (name or "")[:50],
    }


def stcrd_nonstock_row(
    docnum: str,
    seqnum: int,
    docdat,
    stkcod: str,
    party_code: str,
    name: str,
    qty: float,
    unit_price: float,
    amount: float,
    *,
    unit: str = "",
    loccod: str = STCRD_DEFAULT_LOCCOD,
    depcod: str = "",
    ref_no: str = "",
    posopr: str = STCRD_POSOPR,
) -> dict:
    """一条 STCRD 非库存商品明细行(V2·STKCOD=非库存主档码·不动库存/无 COGS)。

    与直接科目行(stcrd_detail_row)区别:STKCOD 是商品主档码(非账码),且 X 字段=净额
    (XUNITPR=单价/XTRNVAL=金额·按 Express 原生非库存写法·实证 HP580101 非零)。无 COGS
    /无库存来自 STKTYP∈{3,4,5} 非库存主档 + LOT 字段全 0(本行不写 LOT*/MREM*/PHYBAL→默认 0)。
    """
    row = stcrd_detail_row(
        docnum,
        seqnum,
        docdat,
        stkcod,
        party_code,
        name,
        qty,
        unit_price,
        amount,
        unit=unit,
        loccod=loccod,
        depcod=depcod,
        ref_no=ref_no,
        posopr=posopr,
    )
    row["XUNITPR"] = float(unit_price)
    row["XTRNVAL"] = float(amount)
    return row


def stcrd_stock_out_row(
    docnum: str,
    seqnum: int,
    docdat,
    stkcod: str,
    party_code: str,
    name: str,
    qty: float,
    unit_price: float,
    amount: float,
    *,
    avg_cost: float,
    cogs: float,
    rem_bal: float,
    rem_val: float,
    unit: str = "",
    loccod: str = STCRD_DEFAULT_LOCCOD,
    depcod: str = "",
    ref_no: str = "",
) -> dict:
    """V2-b 库存出行:真扣客户库存 + 结转 COGS(移动均价·逆向 gold HS580101-001)。

    与非库存/直接科目行相反 —— 带**库存签名**:XUNITPR=均价成本、XTRNVAL=COGS、
    MREMBAL/MREMVAL=卖后结存(故 stcrd_no_stock_impact 返 False)。Express 据此扣库存、认成本。
    STKCOD=客户既有库存商品真实码(STKTYP=0),不是我们新建的码。
    """
    row = stcrd_detail_row(
        docnum,
        seqnum,
        docdat,
        stkcod,
        party_code,
        name,
        qty,
        unit_price,
        amount,
        unit=unit,
        loccod=loccod,
        depcod=depcod,
        ref_no=ref_no,
    )
    row["XUNITPR"] = float(avg_cost)  # 均价成本(≠0 → Express 结转 COGS)
    row["XTRNVAL"] = float(cogs)  # COGS = qty × 均价
    row["MREMBAL"] = float(rem_bal)  # 卖后库存结存(带库存签名 → 真动库存)
    row["MREMVAL"] = float(rem_val)
    return row


def stcrd_stock_in_row(
    docnum: str,
    seqnum: int,
    docdat,
    stkcod: str,
    party_code: str,
    name: str,
    qty: float,
    unit_cost: float,
    *,
    val: float,
    rem_bal: float,
    rem_val: float,
    unit: str = "",
    loccod: str = STCRD_DEFAULT_LOCCOD,
    depcod: str = "",
    ref_no: str = "",
    posopr: str = STCRD_POSOPR,
) -> dict:
    """一条 STCRD 入库行(库存签名·镜像 stcrd_stock_out_row 但方向为「进」)。

    把货以真实单位成本入库:TRNQTY/XTRNQTY=+数量、XUNITPR=单位成本、XTRNVAL=**本行**入库货值
    (val)、MREMBAL/MREMVAL=入库后结存(带库存签名 → Express 认作真实库存·可卖),故
    stcrd_no_stock_impact 返 False。party_code=供应商码(期初无对手方则传空)。
    val 与 rem_val 分开传:期初从 0 起算两者相等,进货入既有库存时 rem_val=旧货值+val。
    金额单一事实源:caller 已用 decimal 量化好 val/rem_val,行内直接复用(与兄弟
    stcrd_stock_out_row 同约定·不在行层重算/float 取整·守「钱用 decimal」基线)。
    """
    row = stcrd_detail_row(
        docnum,
        seqnum,
        docdat,
        stkcod,
        party_code,
        name,
        qty,
        unit_cost,
        float(val),
        unit=unit,
        loccod=loccod,
        depcod=depcod,
        ref_no=ref_no,
        posopr=posopr,
    )
    row["XUNITPR"] = float(unit_cost)  # 单位成本(≠0 → 带成本基础)
    row["XTRNVAL"] = float(val)
    row["MREMBAL"] = float(rem_bal)  # 入库后库存结存(库存签名 → 真动库存)
    row["MREMVAL"] = float(rem_val)
    return row


def stcrd_opening_row(
    docnum: str,
    seqnum: int,
    docdat,
    stkcod: str,
    name: str,
    qty: float,
    unit_cost: float,
    *,
    rem_bal: float,
    rem_val: float,
    unit: str = "",
    loccod: str = STCRD_DEFAULT_LOCCOD,
    depcod: str = "",
) -> dict:
    """一条 STCRD 期初入库行 = 无对手方的入库行(stcrd_stock_in_row 的期初特例)。

    期初从 0 起算,故本行货值 == 入库后结存货值(rem_val);PEOPLE 空(期初无供应商)。
    """
    return stcrd_stock_in_row(
        docnum,
        seqnum,
        docdat,
        stkcod,
        "",
        name,
        qty,
        unit_cost,
        val=rem_val,
        rem_bal=rem_bal,
        rem_val=rem_val,
        unit=unit,
        loccod=loccod,
        depcod=depcod,
    )


_STOCK_IMPACT_FIELDS = (
    "PHYBAL",
    "MREMBAL",
    "MREMVAL",
    "LOTBAL",
    "LOTVAL",
    "LUNITPR",
    "MLOTNUM",
)


def stcrd_no_stock_impact(rec) -> bool:
    """该 STCRD 行是否零库存影响:所有 LOT/库存余额字段为 0/空(=不动库存、不产 COGS)。
    缺字段(合成表 vs 真表)宽容跳过。直接科目行与非库存行都应满足。"""
    for f in _STOCK_IMPACT_FIELDS:
        try:
            v = rec[f]
        except Exception:
            continue
        if isinstance(v, str):
            if v.strip():
                return False
        elif v and float(v) != 0:
            return False
    return True


def _norm_dir(p: str | Path) -> str:
    """目录归一(分隔符 + 尾斜杠 + 大小写):UNC 正/反斜杠差异抹平后精确比。"""
    return str(p).replace("/", "\\").rstrip("\\").lower()


def path_ambiguity(p: str | Path) -> str:
    """路径若会被系统"再解释一遍"就返回解释后的真实落点;不会则返回空串(安全)。

    Windows 致命坑:UNC 少写一个反斜杠(`\\\\srv\\share` 写成 `\\srv\\share`),系统当成
    **当前盘根目录**下的路径(`C:\\srv\\share`)。这类路径不会报错 —— 只会把记账静默写进
    本机一个假目录,程序一路报"成功",几个月后才被发现账没进 ERP。2026-07-23 真实踩过:
    机器上存在 `C:\\accserver\\ACCOUNT\\...` 这么一棵影子账套树,把排查带偏了大半天。

    判据:`abspath` 后与原值不同 = 系统重新解释过 = 危险。
    """
    s = str(p or "").strip()
    if not s:
        return ""
    resolved = os.path.abspath(s)
    return "" if _norm_dir(resolved) == _norm_dir(s) else resolved


def account_dir_allowed(account_dir: str | Path, selected_dir: str = "") -> bool:
    """写盘目标目录闸:目标须**精确** == 客户所选账套目录(配对锁定)且路径无歧义。

    `selected_dir` = config.account_dir(客户从探测列表选的那家)。这不是"批不批准",
    是"别写错地方":目标 == 所选 → 放行;不等 → 拒(防我们映射 bug 串账套)。
    缺所选(空)→ **fail-safe 拒写**(没选不能瞎写)。无 DATAT 写死、无白名单回落。
    另拒"会被系统改写的路径"(见 path_ambiguity)——否则写进本机影子目录还报成功。
    """
    s = (selected_dir or "").strip()
    if not s:
        return False
    if path_ambiguity(account_dir) or path_ambiguity(s):
        return False
    return _norm_dir(account_dir) == _norm_dir(s)


# 写入器追加/更新时要写的表(STCRD 存货卡留增量,本期非存货/费用不碰)。
WRITE_TABLES = ("ISRUN", "APTRN", "ISVAT", "GLJNL", "GLJNLIT", "APBAL")

# 写前备份的 allowlist:覆盖所有写路径会 mutate 的表 —— 采购/销项过账表
# (WRITE_TABLES + SALES_WRITE_TABLES)+ 自建主档(APMAS/ARMAS)+ 存货(STCRD/STBAL,
# 当前不写但留前向兼容)。漏一张 → 回滚不全,故宁可多备。选择性备份只复制其中存在的表,
# 远小于整账套 255 文件(网络盘上 copytree 整目录是推送慢的根因)。
BACKUP_TABLES = (
    "ISRUN",
    "APTRN",
    "ARTRN",
    "ISVAT",
    "GLJNL",
    "GLJNLIT",
    "APBAL",
    "ARBAL",
    "APMAS",
    "ARMAS",
    "STCRD",
    "STBAL",
    # V2:ensure_item 建非库存主档 + 科目映射,纳入回滚范围(写失败回滚要撤掉建出的主档)。
    "STMAS",
    "ISACC",
)

# 按方向只备份这张单会写的表(2026-09-28 压测:每张单整套备份 14 张表是推送慢的一处)。
# 进项不写 ARTRN/ARBAL/ARMAS,销项不写 APTRN/APBAL/APMAS;其余(流水号/税/分录/库存/主档)两边都可能写。
PURCHASE_BACKUP_TABLES = tuple(t for t in BACKUP_TABLES if t not in ("ARTRN", "ARBAL", "ARMAS"))
SALES_BACKUP_TABLES = tuple(t for t in BACKUP_TABLES if t not in ("APTRN", "APBAL", "APMAS"))

# 合成表规格搬去 table_specs(本模块只留真机写盘要用的口径);import 路径不变。
TABLE_SPECS = _TABLE_SPECS


# 余额表月桶列名搬去 fiscal_period:桶列号是 ISPRD 财年期号(还带 CY/NY 两个年槽),
# 这里原先那两个函数从单号里取日历月、恒写 CY 组,对非 1 月起账的账套 100% 错位,对 CY
# 不等于当前公历年的账套整年错槽。


def be_yymmdd_to_greg(yymmdd: str) -> _dt.date:
    """佛历 YYMMDD(2 位年)→ 公历 date。例 '581231' → 2015-12-31。

    2 位佛历年按 25xx 解:greg = 1957 + yy(58→2015, 69→2026)。
    """
    s = str(yymmdd or "").strip()
    if not (len(s) == 6 and s.isdigit()):
        raise ValueError(f"bad BE yymmdd: {yymmdd!r}")
    yy, mm, dd = int(s[0:2]), int(s[2:4]), int(s[4:6])
    return _dt.date(1957 + yy, mm, dd)
