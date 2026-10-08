# 推送值按账套数据规则定，少问人（2026-10-08）

分支 `pearnly/push-rules`，基线 `f83e059e`（= origin/main）。用户 2026-10-08：「哪些真正影响业务、哪些不重要；不要把不重要的都加进去
让人兜底；哪些能用规则框死；哪些写死会导致其它事务所用不了」→ 批准推荐方案，并要求部门与 MR.ERP 一起改、自动录入规则沿用。

## 依据

- 审计：Express 推送路径写死 / 默认值逐项对照测试机 62 套真实账套（只读，`C:\PearnlyExpress\70EXP`）。
- 原生记法核实：事务所真账（S: 盘，只读复制少量 DBF 解析，下班后跑，临时文件已删）8 套 —— HP 2165 张、HS 13946 张、
  RR 4397 张、IV 6828 张。
- MR.ERP：源码只读 `/Users/skin/mrerp-source/src`（网页新建单据默认、`*/component/dupdata.php`「复制现有档案」）。

## Express（小助手写入时按账套自己的数据，不问人）

| 改动 | 依据 | 位置 |
|---|---|---|
| 赊购 / 赊销的应付 / 应收行记对方档上的科目（不在科目表或没挂才用账套默认） | 62 套里 44% 客户、16% 供应商挂的不是默认科目；Express 过账用档上的 | `account_rules.control_account`；`dbf_writer` / `dbf_sales` `_write_all` |
| 现购 HP 贷现金、现销 HS 借现金（ISINFO.ACCNUM00），不碰应付 / 应收 | 真账 HP 0/2165、HS 0/13946 碰控制科目 | 同上 |
| 到期日 = 单据日期 + 对方档付款期，单据 PAYTRM 一并写（老合成表没有这列不写） | 真账 RR 97%、IV 99.8% 如此 | `account_rules.party_terms` / `due_date`；`dbf_fields.audit_fields(duedat)` |
| 单据簿：日记账取 ISRUN 该簿的 JNLTYP（6 套 HP 簿是 01）；没给部门时取簿上默认部门，再没有就不记 | 真账表头 82%–90% 不记部门 | `account_rules.book` |
| 存货卡片行单位码查本账套单位表、仓库取 ISINFO.MAINLOC；内置单位对照改正（ชิ้น→ชน、อัน→อน） | 62 套 ชิ 0 套、ชน 62 套；1 套没有 01 仓 | `account_rules.localize_stcrd`；`dbf_schema._UNIT_MAP` |
| 新建客户 / 供应商类型码取本账套最常用的（不再写 00） | 00 有 2 套没有、4 套另有含义 | `account_rules.common_type`；`master_create` |
| 删除写死兜底科目 21-02-01-00 / 11-02-01-00，缺科目取账套默认（ISINFO.ACCNUM07 / 02），再没有就停下 | 28 套没有这两个码 | `master_create` |
| 现金客户：有 เงินสด 用它，否则认本账套唯一的现金客户（按名称），再没有才建 เงินสด（客户档科目 = 默认应收、类型 = 最常用） | 56 套没有 เงินสด | `master_create.ensure_customer` |
| 期末盘存制账套（ISPERPETUA=N）：采购借「购货」科目 ACCNUM18，不借存货、不做重分类 | 7 套期末盘存制 | `dbf_writer._write_all` |
| 录入人写固定的 `PEARNLY`（不再写 BIT9 —— 测试事务所的真人账号） | 真账单据 26 个录入人大多不在用户表，Express 照常打开 | `dbf_schema.PEARNLY_USERID` |

服务端：部门不再要人选（`push/express/choices.py`），选过的照用；自动录入规则里设的部门在这份账套还没记过时照规则记下
（`folder_intake/push.py`）。其余写入规则全在小助手，服务端载荷不变（新旧版本互相兼容：旧小助手照旧写法）。

## MR.ERP（少问人）

- 部门 / 项目 / 仓库 / 员工 / 销售区 / 运输：没选过跟 MR.ERP 网页新建单据的默认（按代码第一条）；本公司分店取总店（赊销新建页预填值）。
- 新建客户 / 供应商 / 商品的类型、应收 / 应付科目、分类、单位：照这家公司最新 30 份现有档案里占 60% 以上的那个（MR.ERP「复制现有档案」同源）。
- 银行收款科目：科目表里只有一个「เงินฝาก…」就用它。
- 还会问的：多个存款科目时的银行科目、费用物料的费用科目、现有档案没有明显多数的类型 / 科目 / 分类 / 单位。

## 不做（用户 2026-10-08 认可「不重要」）

多本单据簿、凭证号格式与公司编号规则不同、销项单头业务员 / 区域 / 送货方式、新建对方的价目表号等留空、新建对方科目随账套默认。

## 验证

- 小助手 604 通过（新增 `test_account_rules.py` 7、写入层 5：对方档科目、HP / HS 现金、期末盘存制、现金客户）。
- `test_cowork_mrerp_masters` 10、`test_cowork_express_choices` 9、Development checks 各步 0 失败；Cowork 全量与 main 失败项相同。
- 生产镜像（测试机，挂本工作树源码）：mrerp_push 39、mrerp_menus 17、express_choices 9、folder_intake 18、push_lifecycle 30、
  push_native_contract 12、push_contract 19、targets 39、line_intake 27、express_mapper 31、express_sales_mapper 31、
  express_preflight 23 等全部通过；`ci-candidate-checks.sh offline` 通过。
- 62 套真实账套副本离线写入回归（只写临时副本，原件未动）：每套写 RR / HP / IV / HS 合成单 242 张，239 张成功且逐项核对通过
  （科目存在可记账、控制科目、现金科目、到期日、单位 ชน、主仓、类型码、PEARNLY、期末盘存 7 套记购货）。3 张失败是账套里
  个别行带 cp874 解不出的字节（3 套）→ 改为读表时替换坏字节（`S.UNICODE_ERRORS`，原字节不动）；另发现新建现金客户没标
  新建主档 → 已修。修后 8 套复跑（含失败的 3 套）32 张全过，原有记录逐字节未变。
- 未验证：Express 的 CDX 索引重建与回查（需 Windows 上的重建程序）、Express 界面打开、库存模式明细。
