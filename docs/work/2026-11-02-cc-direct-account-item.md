# CC 直接科目行：照原生先有「编码 = 科目码」的商品（11-02）

分支 `pearnly/cc-direct-account-item`（基线 main 81fa4837）。主会话 11-02 派查、定修法：只改 CC、不改载荷，随 CC 1.12.11 发。

## 现象

车架号窗口 11-02 在服务器版 Express 69EXP\TEST 实录：CC 直写的现购 HP，明细是「直接科目行」（STKCOD 放科目码 `11-04-02-00`），
在 Express 里原生作废弹 Message#. 2101「找不到商品编码 [11-04-02-00]」，单头 DOCSTAT 变 **E**、画面「ยกเลิกไม่สมบูรณ์」（作废不完整），
金额、凭证都没动。客户在 Express 里作废 CC 写的这种单会卡住。

## 根因（只读证据）

- **Express 原生明细的商品码一律在商品主档里**：测试机 S 盘副本 1037 本只读扫，原生明细 2,597 万行，只有 1,174 行例外
  （690 行码是科目码却不在主档、484 行两样都不是，多半是商品后来被删了）。
- **原生「直接记科目」的做法**：会计先建一件编码就是科目码的非库存商品（ACCCOD 经 ISACC 联到这个科目），单据行引用它。
  这种商品原生 20,318 件：STKTYP 3 有 10,809、5 有 9,158、0 有 322、4 有 29。
- **CC 的直接科目行**（`dbf_detail` direct_account，`dbf_schema.stcrd_detail_row`）只把科目码写进 STKCOD，主档里没有这件商品；
  Express 原生作废按 STKCOD 去 STMAS 找商品，找不到就报 2101。差的不是哪一栏，是缺了原生先建的那件商品。

### 哪类单、哪种行

直写线写出的直接科目行（两条直写线都有，载荷都带 `item_mode = direct_account`，写入是同一份 CC 代码）：

- 人选了费用科目的采购（RR / HP；Cowork 复核选的、ERP 问的费用科目，`mapper` 里 `choices.get("account")` 那一条，9-28 起不再按行建服务存货）；
- 销项按收入科目记的直接科目行（IV / HS，`sales_mapper`）、退货 SR 里同样的行；
- 建非库存主档失败回落的直接科目行。

不受影响：ERP 前台认出、编码本身就是科目的 Express 服务类物料（`prepare._own_accounts`，那件商品本来就在主档）；代录（在 Express
界面里录，用主档里的商品）。

### 存量（只读数）

- 测试机 C 盘 CC 写过的测试账套：7 本 53 行（RR 20、HP 33），如 T8GIZE 的 `5370-06`；这些账套里的原生行都在主档里。
- 生产（回执逐行 mode，按站点 / 账套）：
  - Cowork **ccdemo**（演示）：`\\accserver\d$\account\70exp\test` 18 张约 50 行（10-20 ~ 10-21）、`\\pc-afa21e\c$\account\70exp\demo01` 9 张约 81 行（10-20）；
  - Cowork **testcowork**（测试）：`c:\account\70exp\demo01 / 02 / 03` 9 / 19 / 19 张约 13 / 43 / 51 行（10-13）、测试机 C 盘 T 账套
    `t8niexcp / t9asc / t9niex12 / t9niexcp` 1 / 1 / 3 / 8 张（10-15 ~ 10-24）；
  - ERP：所有站点写账件载荷里没有直接科目行（只有 testerp 有 6 件写账，都不是）。
  - 没有真实客户的账套。

## 修法（只改 CC，载荷不升版本）

1. `product_master.ensure_account_item`：写直接科目行前，保证主档里有编码 = 科目码的非库存商品——已有就复用（3 / 4 / 5 型都算，
   人家的不动）；同码已是库存品（0 型）抛 `AccountItemIsStock`，回执码 `ACCOUNT_CODE_IS_STOCK_ITEM`、outcome `needs_review`，
   整单回滚交人；没有就照 `ensure_item` 的建法建（克隆同账套非库存品、缺的按原生服务品形状补、STKTYP 3、品名取科目名、
   ACCCOD 经 ISACC 联到同一科目）。这一行按原生非库存商品行写（`stcrd_nonstock_row`），凭证 GLJNLIT 照旧由 CC 显式写，账务不变。
   建这件商品本身失败时才退回只写科目码的旧写法，原因随回执带回（`account_item_failed`）。
2. **存量补建**：每本账套第一次写带明细的单时，在同一张单的写前备份里，把 CC 写过（单头 USERID = PEARNLY）、码在科目表里却不在
   主档的那些科目码补建成商品（`product_master.backfill_account_items`）；这张单提交成功才在本机记「补过了」
   （`%APPDATA%\Pearnly\account_items.json`），回滚了下次再补。会计自己录的、商品后来被删了的旧单不碰。
3. 服务端只加了词条（闸门 `test_cowork_reason_language` 要求 CC 每个错误码都有）：Cowork `intake.error.ACCOUNT_CODE_IS_STOCK_ITEM`、
   ERP `express.error.ACCOUNT_CODE_IS_STOCK_ITEM`，中文「此科目码已是 Express 库存品的编码」（新增 2、标黄 0）。CC 回执带 outcome
   `needs_review`，服务端照转人工；没加进 `ack_rules.NEEDS_PERSON_CODES`（那是给不带 outcome 的老版 CC 用的，老版不会回这个码）。

## 验证

- CC 全套离线 1406 + 新增：`companion/tests/test_account_items.py`（建一次、复用、人家建的 5 型照用不动、库存品同码整单回滚交人、
  CC 写的旧行下一张补建且只补一次、会计录的旧单不碰、回滚后下次再补）、`test_dbf_writer_sales.py` 销项库存品同码交人、
  建商品失败才只写科目码；改了三条原来断言旧写法（直接科目行 X=0、主档里没有商品）的用例。
- **实机三段（录制机 rec-laptop + 服务器版 Express 69EXP\TEST，11-02 下午；先整本快照、测完整本还原并核哈希）**：
  1. 主线现写法（81fa4837）写 HP691102-001、-002（STKCOD `11-04-02-00`、主档里没有）；Express 原生作废 -001 → 弹
     Message#. 2101「找不到商品编码 [11-04-02-00]」，按 ตกลง 后 DOCSTAT **E**（复现）。
  2. 本分支写 HP691102-003：写前先补建了 `11-04-02-00`（STKTYP 3、ACCCOD P002、品名取科目名），这张的行引用它、按非库存商品行写
     （XTRNVAL = 净额）。
  3. Express 原生作废 **-002（旧写法、补了商品）→ C、金额 0、没有弹框**；**-003（新写法）→ C**；停在 E 的 **-001 再作废一次也 → C**
     （已卡住的单补了商品后也能作废完）。
  - 收尾：Express 正常退出；TEST 按快照整本还原（13 个文件）、哈希全对、无金额 0 的空单头。
- 测试机 C 盘 T 账套离线回归：车架号窗口当时占测试机，没跑（待补）。
