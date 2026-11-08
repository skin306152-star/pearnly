# 需求影响评估：Cowork 推单选 Express 仓库

> **审定：主会话 11-07 审过，第 12 节 7 条全部按建议定。** 排在 CC 连接 / 公司选择 D 步合入 `main` 之后由本窗口施工（主会话通知开工）；~~CC 部分与共用电脑隔离同一版（1.12.17）发~~ → 11-08 改随 CC 1.12.18 发（施工记录见文末）。

> 按 [需求影响评估模板](../development/templates/impact-assessment.md) 填写；**只写评估，不改代码**，交主会话审定。
> 基线 `origin/main` = `8e5cb5724`（11-07，已含 Cowork 按行选物料 `5cfe2abb3`）；分支 `pearnly/deferred-three-assess-42d230`。
> 路径缩写：`C/` = `apps/pearnly_cowork/pearnly_cowork/`，`K/` = `apps/pearnly_core/pearnly_core/`，`E/` = `apps/pearnly_erp_portal/pearnly_erp_portal/`，`cc/` = `companion/src/companion/`。
> 来由：[按行选物料评估](https://github.com/skin306152-star/pearnly/blob/pearnly/line-item-stock-choice-770877/docs/work/2026-11-06-erp-line-item-stock-choice-评估.md) 第 15 节 C5（「Cowork 不选仓库，以后另评估」），用户要求排进来做。
> 证据：只读代码；Express 原生按行带仓的统计见第 4.1 节（11-07 测试机账套副本只读统计，主会话批准与混行窗口并行）。没碰生产、实验栈。

## 结论先说

1. **CC 桌面版早就会按仓写**（载荷 4，10-22 起）：行上 `loccod` 优先、单头是默认，仓库码必须在这本账的仓库代码表（ISTAB 21）里，否则整单 `ERR_LOCATION`、不回落主仓；
   没带仓的行写主仓 `ISINFO.MAINLOC` 并照样维护 STLOC（`cc/stock_location.py:1-20,124-145`、`cc/payload_contract.py:19-21`）。**CC 写入不用改、载荷不用升版本**。
2. **共用映射器也已经会带**：库存行有 `loccod` 就透传、盖载荷 4（`K/express_rules/push/common.py:257,343-345`）。Cowork 不带，只是因为它的行从识别来，没有仓。
3. **真正缺的是两样**：Cowork 拿不到账套的仓库表（心跳目录只有存货、客户、供应商、部门、存货科目组，`cc/catalog_probe.py:284-321`、`C/connections/reports.py:348-361`）；Cowork 没有「哪一行进哪个仓」的选择和记忆。
4. **建议做法**：
   - 仓库表照部门的做法随心跳目录报（CC 加一个 `probe_warehouses`：ISTAB 21 全部仓 + `MAINLOC` + 本年度 STCRD 出现过的仓；STCRD 本来每次目录都整表扫一遍算指纹，`cc/catalog_probe.py:162-171`，顺手记下 LOCCOD，不多一次读）→ Cowork 存 `reported_warehouses`。**要发 CC 桌面版新版**；旧版不报，Cowork 就当没有仓库表、照旧写主仓（同部门「旧版不报不写空表」的做法，`reports.py:353-356`）。
   - 只有**账套真的在用两个以上仓**时才出现仓库选择。测试机 615 本真账（第 4.1 节）仓库代码表**全都**有两个以上仓（339 本就是 Express 模板自带的 01、02），按代码表判会让每家都被问；
     所以 CC 报的是「在用的仓」：本年度 STCRD 里出现过的仓 + 主仓。按这个口径约 1/4 的账套（151 / 615）会出现选择，其余照旧写主仓、界面不变。
   - 预选：人选的 → 这一行的记忆（同存货对照的键）→ 这家公司账套的默认仓（记一次，同部门 `subject = "*"`）→ 主仓。默认仓没记过时**和按行选物料的那一问合在一起问一次**，不另起一问。
   - 各入口（LINE、网页复核页、CC 直写、CC 代录、定时任务 / 自动推）都走同一份判定。
5. 工作量约 9 小时写码 + 机器跑（第 13 节），要发一版 CC 桌面版（只是多报一张小表），服务端先部署、CC 再发。

## 0. 需求原文与理解

- 来源：按行选物料评估 C5「不加，照旧写主仓…加仓库要 Cowork 读 Express 仓库表、按公司记默认仓，另评估」，主会话 11-06 审定时说「以后要再另评估」；11-07 用户要求所有待办都排进去做，主会话派活。
- 我的理解：事务所给客户在 Express 记采购 / 销售时，客户的 Express 账套如果有多个仓（例如总店仓和分店仓），库存行要写进对的那个仓；现在全写进主仓，各仓结存就错了。
- 不在范围：
  - ERP 产品（已按仓推，`E/express/push.py:180-196`）。
  - MR.ERP 目标：导入表本来就有「คลัง」栏（`K/mrerp/push/xlsx/purchase.py:62`、`sales_credit.py:284`、`sales_cash.py:63`），值取 MR.ERP 默认值里选过一次的 `store`（`K/mrerp/push/defaults.py:24,108`、`K/mrerp/masters.py:38,72`），已经有「按公司选一次仓」，本需求不动。
  - 仓位（Express 只有「仓」一级，没有仓位；Cowork 也不做仓位，同 C5）。
  - 调拨单（RL）：Cowork 不建调拨单。
  - 不改识别提示词（票面上一般没有对方 Express 的仓库码）。

## 1. 现有同类与复用

| 同类 | 现状 | 复用什么 | 出处 |
|---|---|---|---|
| CC 按仓写 | 载荷 4，采购 RR/HP、销售 IV/HS、出入库 OU/JU/TK、退货 SR 都走 `stock_location.plan`；非库存行固定主仓 | 直接用，不改 | `cc/dbf_writer.py:341-348`、`cc/dbf_sales.py:263-279`、`cc/dbf_stock.py:326-330`、`cc/dbf_refund.py:272-279`、`cc/dbf_detail.py:207` |
| ERP 仓库对照 | `ERP Warehouse.express_code`，按公司；仓库表来自按需请求 `express_masters`（ISTAB 21 + STLOC + MAINLOC） | 读法（`cc/express_masters.py:9-16,56,240`）可直接搬到心跳目录 | `E/express/masters.py:674-700` |
| Cowork 部门 | 心跳目录报 `departments` → `reported_departments`；整份账套记一个（`subject = "*"`）；不问，没选过由 CC 取单据簿默认 | **仓库照这条路走**：上报、存储、`chosen(kind, "*")`、公司规则垫底 | `cc/catalog_probe.py:142-156`、`K/express_rules/push/choices.py:210-217`、`C/push/express/company_choices.py:29-30` |
| Cowork 按行选物料 | `item` 每行一问（含「新建存货」「记费用」），记忆键 `方向|对方|品名` | 仓库跟着这一问出，记忆用同一个键 | `K/express_rules/push/choices.py:252-312`、`C/assistant/precheck_source.py:228-249,318-337`、`C/line/items.py:63-80,140-156` |
| CC 代录 | 行的 `loccod` → 单头 `loccod` → 都没有就不输入、用 Express 自己带出的默认仓；有值才回读核对 | 载荷带上仓就行 | `cc/express_operator/draft.py:140-150`、`engine.py:710-711`、`book.py:187-188` |

结论：**扩展现有**，不新建表、不新建问法。

## 2. Frappe 原生能力

- 网页复核页的仓库选择用原生 `Select`（只列这本账的仓，通常个位数），批量编辑沿用按行选物料已加的原生 Dialog + Grid。
- 记忆存现有 `Cowork Express Choice`：`kind` 是写死的下拉（`cowork_express_choice.json:60`），加一个 `location` 选项要随 DocType 改并走原生迁移。
- 无自定义样式。

## 3. 按类通用

- 类别：**Cowork 推进 Express 的所有带库存行的单**——采购（RR / HP）、销售（IV / HS）、销售退货（SR，Cowork 走的话）。
- 只有库存行带仓；记费用 / 非库存行不带（CC 也固定写主仓）。
- 不覆盖：MR.ERP（已有按公司选一次的默认仓，见第 0 节）、调拨（Cowork 没有）、盘点（Cowork 盘点模块只有 `location` 文本，`C/stocktake/contracts.py:12`，不推 Express 库存单）。

## 4. 上下游数据

### 4.1 Express 原生：真账里多仓、按行带仓有多常见

**做法**（11-07 15:09–15:13，主会话批准与混行窗口并行）：以 macadmin 低优先级只读测试机本机 `C:\PearnlyStress\S盘副本`（2564–2569）与 `accserver副本`，
同名账套（按月复制的）只留 STCRD 最大的一份，共 **615 本**；读 ISINFO.MAINLOC、ISTAB 21、STLOC、STMAS.STKTYP、STCRD。「库存行」= STMAS.STKTYP 0 的品；单别按 DOCNUM 前两位（RR / HP / IV / HS）。
脚本只读打开 DBF，跑完临时目录 `C:\PearnlyStress\_loc1107` 已删、回读不存在；脚本与逐本结果在共享笔记 `仓库统计-11-07/`。

| 项 | 结果 |
|---|---|
| 仓库代码表（ISTAB 21）有两个以上仓的账套 | **615 / 615**（339 本正好是 01、02 两个，即 Express 模板自带；4 个以上的 217 本） |
| 主仓 MAINLOC | 01：555 本；02：25；1001：24；0001：6 |
| STLOC 里两个以上仓有结存 | 398 本 |
| **有库存单写进非主仓的账套** | **151 本（25%）**；其中非主仓单占这本账库存单 <10% 的 114 本、10–50% 的 19 本、≥50% 的 18 本 |
| 库存单写进非主仓的单数 / 全部带库存行的单 | RR 34,896 / 312,965（11%）；IV 82,372 / 835,574（10%）；HS 2,145 / 1,035,967（0.2%）；HP 23 / 13,584 |
| 同一张单的库存行分在两个以上仓 | RR 348、IV 1,717、HS 577（都不到 0.5%） |

读法：
- **代码表不能当判断依据**：每本账都有两个以上仓，按它出选择就是每家都问。要按**实际用过的仓**判（本年度 STCRD 出现过的仓 + 主仓）。
- 真在用多仓的约四分之一账套；这些账套里人录的进货、销货约一成进了非主仓，Cowork 现在全写主仓，这部分各仓结存就是错的。
- 同单分仓极少，一张票一个仓的假设成立（支持待定 #5「LINE 不逐行问仓」）。
- 这些非主仓单都是人录的（Cowork 推的一律主仓），所以比例反映的是客户真实的用法。STCRD 没有录入人一栏，没能再按人分。

旁证（代码注释，10-22 只读核过 5 套事务所真账）：69SPP206 的 02 / 30 两个仓都在动（`cc/stock_location.py:12-13`）。

### 4.2 现在写主仓会错在哪

- 采购：库存全进主仓，其他仓的 STLOC 结存偏少、主仓偏多；STMAS 总结存不受影响。
- 销售：没带仓时 CC 按**全仓合计**核负库存（`cc/stock_guard.py:116-123` `located_lines` 为假），能写进去，但从主仓扣，主仓可能变负、别的仓虚高。
- Express 里按仓看库存、按仓盘点、按仓出报表都会错；客户自己在 Express 里录单时选的仓和我们写的对不上。

### 4.3 改后数据流

```
CC 心跳目录（新版多报 warehouses：全部仓 + 主仓 + 在用的仓）
  → Cowork 账套行 reported_warehouses（旧版不报：不写空表）
  → 推送准备 / 开始前确认卡片：库存行定仓（预选顺序见第 6 节）
  → 映射器在库存行写 loccod（K/express_rules/push/common.py:343-345 已透传）→ 载荷 4
  → CC 写入（stock_location.plan 校验在 ISTAB 21 里）/ 代录（draft.py 取 loccod）
```

- 载荷：带 `loccod` 盖 4，现在的 CC 支持 1–19（`cc/payload_contract.py:48`），**不升版本**。注意 `finalize_payload` 只看行上的 `loccod`（`common.py:257`）：本方案每个库存行都写 `loccod`，不放单头，就不用另外处理升版本（单头的写法要像 `depcod` 那样自己升，`K/express_rules/push/choices.py:435-444`）。
- 仓库被删 / 改码：CC 写前校验，不在表里整单 `ERR_LOCATION`（不回落）；Cowork 收到这个错按现有「推送失败可重推」处理，并让记住的默认仓失效（下一拍目录里没有就不再套用，同部门 `value in _codes(departments)` 的判断，`choices.py:215`）。
- ERP：不涉及。Cowork / DMS 其他模块：不涉及。

## 5. 隔离与权限

- 仓库表跟账套走（每本账一行 `reported_warehouses`），只有能用这条连接、这份账套的人看得到，和部门、存货同一个口径（`connections.access`）。
- 记住默认仓 = 改账套记忆：同现有记忆的权限（能推这家公司的人可以改，变更表留痕，`C/push/choices.py`）。公司规则那一层同部门。
- 服务端判：推送前用 `reported_warehouses` 校验选的仓在不在表里；前端只显示。

## 6. 入口

**判定（所有入口共用一份纯函数，放共用层 `K/express_rules/push/choices.py`，与 `item` 同处）**

| # | 条件 | 结果 |
|---|---|---|
| 0 | 这本账报来的**在用仓**（本年度 STCRD 出现过的仓 + 主仓）没有或只有一个；或这一行不是库存行 | 不带 `loccod`（写主仓，同今天），不问 |
| 1 | 复核页 / 卡片上人已选 | 照人选的 |
| 2 | 这一行的存货对照记忆（键 `方向|对方|品名`）带着仓，仓仍在表里 | 照记住的 |
| 3 | 这家公司这份账套记过默认仓（`location`，`subject = "*"`；同公司各年度账套通用，同 `item`），仍在表里 | 照默认仓 |
| 4 | 其余 | **预选主仓**，要人确认一次；确认的结果记成默认仓 |

只有第 4 条要人看一眼，而且一家公司一般只看一次。

**各入口**

| 入口 | 改后 |
|---|---|
| LINE | 按行选物料的识别后追问里（「全部新建存货 / 全部记费用 / 逐项选择 / 取消」，`C/line/items.py`）：有库存行、走到第 4 条时，**同一段追问里接着问一次仓**（快捷回复列仓，最多 13 个；更多时打字找，同账套搜索），答案记成默认仓。逐项选择时不逐行问仓（一张票通常进一个仓），要改单行去网页复核页 |
| 网页复核页 | 库存行在「过账类型」旁多一个仓库下拉（只在多仓账套出现），按上表预填；批量编辑对话框加一栏「仓库」（整批设）；推送前的统一卡片：走到第 4 条时和存货对照同一张卡里列「仓库」一项 |
| 网页新建录入 | 不加（上传前不问，同 C7 的思路） |
| CC 直写 | 开始前确认卡片的 `item` 项每行带仓（`C/assistant/precheck_source.py`）；走到第 4 条时卡片里多一项「仓库」（预选主仓），点「开始」即确认并记住 |
| CC 代录 | 载荷带 `loccod` 后代录照 `draft.py:140-150` 录进那个仓并回读核对；资料区不变 |
| 定时任务 / 自动推 | 第 1–3 条能定就照推；走到第 4 条时**照推主仓、不拦**（任务级默认 = 主仓，同今天），不新增 `choice_needed`。理由：多数单人不在场，今天就是主仓，不能因为加了仓库让自动推停下（同 C10 口径「默认只填没定的」）。待定 #4 |

- 手机：CC 网页、LINE 同上；Desk 复核页窄屏照原生 Select。相机：不涉及。

## 7. 共用件与连带回归

- 改到共用层 `K/express_rules/push/choices.py`（仓库判定、`location` 记忆）、`K/express_rules/push/common.py`（只用现有透传，原则上不改）：**合入前在生产镜像跑候选离线检查**（共用层改动规矩）。
- 登记的共用件：`cowork.page.intake`（复核页、`targets.js` 选择框）、`cowork.express.push`、`cowork.line.chat`、CC 开始前确认卡片、CC 设置连接（CC 新版）。施工时 `python3 scripts/check-impact.py --files ...` 出清单逐项实测。
- CC 桌面版：`catalog_probe.py`、`tomo_bridge` 功能标记不用加（心跳字段新增，旧服务端忽略）。`tests/test_companion_contract.py` 守接口，要补「目录带 warehouses」一条。
- 冒烟 / 流程：新增 `cowork.express.warehouse`（多仓账套：LINE 追问出仓、复核页下拉、载荷带 `loccod`、单仓账套不出现）。

## 8. 存储与成本

- 每本账多存一张仓库表（通常 1–10 行，每行码 + 名，几百字节）；心跳多读一次 ISTAB（已经在读部门同一张表，同一次打开可一起读）。
- 无外部调用、无识别费、无新增固定月费。

## 9. 删除、作废、追溯

- 作废已推的单：CC 作废按单据原有的行仓回冲（STLOC 维护已有，`stock_location` 文件头），不涉及新逻辑。
- 记住的默认仓：可在 CC 公司卡「记住的选择」里改 / 清（复用现有卡片，`company-cards.tsx` 的 `CODE_KINDS` 加 `location`）。
- 仓库在 Express 里被删：见 4.3，校验失败不回落、记忆失效。

## 10. 文案与官方 UI

新增（施工时 `check-copy.py --report` 出正式清单）：
- 「仓库」（字段名 / 卡片项名；先查术语表有没有现成词条，ERP 已有「仓库」可沿用）。
- LINE 追问的问句一句（例如「入哪个仓？」，按现有 LINE 追问风格，泰中英三语）。
- 不改已有文案。

## 11. 测试与验收

- 单元：判定纯函数 0–4 条各一；仓不在表里不套用；单仓账套不出；旧版 CC 不报仓库时不出；映射器库存行带 `loccod`、非库存行不带、载荷盖 4。
- CC 桌面版：`probe_warehouses` 读 ISTAB 21 / `MAINLOC` / 在用的仓（只有 01 有单的账套报一个在用仓、不出选择）；离线回放 62 套（C 盘测试账套）里多仓账套带仓写入，STLOC 逐仓与 Express 原生记法一致（沿用 10-22 / 10-25 的核对脚本）。
- 实验栈流程：LINE 收单多仓追问、复核页、CC 直写卡片、定时任务照推主仓。
- 测试机：TEST 账套（`\\accserver\d$\ACCOUNT\70EXP\TEST`）先确认有没有两个以上仓（没有就在 Express 里加一个测试仓，要主会话同意），CC 新版 staging 写一张带仓采购、读回 STCRD.LOCCOD 与 STLOC；代录同一张录一次比对。
- 用户要看：LINE 追问里那一问的样子、复核页仓库下拉的位置。

## 12. 要主会话定的点

| # | 问题 | 我的建议 | 不定的影响 |
|---|---|---|---|
| 1 | 做不做 | 做：615 本真账里 151 本（25%）真在用多仓，这些账里约一成进货 / 销货在非主仓（4.1），Cowork 全写主仓是数据错 | 不做就维持 C5 现状：多仓客户各仓结存一直错 |
| 2 | 仓库表从哪来 | 随心跳目录报（CC 发新版，多读 ISTAB 21 + MAINLOC，在用的仓随 STCRD 指纹那一遍顺带记）；旧版不出仓库选择 | 改用按需 `express_masters`（不发 CC）要读整套主档（上限 16 MB，`request_contracts.py:60-61`），每家每次刷新都重，不建议 |
| 3 | 记忆粒度 | 公司账套一个默认仓 + 每行跟存货对照记 | 只记默认仓：同一供应商的某几样东西固定进别的仓就每次要改 |
| 4 | 自动推 / 定时任务走到「没记过默认仓」 | 照推主仓、不拦（同今天） | 改成留待处理会让现在能自动推的单停下 |
| 5 | LINE 逐项选择时要不要逐行问仓 | 不逐行问，一张票一个仓；要分仓去网页复核页改 | 逐行问会让 LINE 追问变长 |
| 6 | 什么时候出现仓库选择 | 按「在用的仓 ≥ 2」（本年度 STCRD 出现过的仓 + 主仓），约 1/4 账套出现 | 按仓库代码表判：615 本全都 ≥ 2，每家都会被问一次 |
| 7 | 排期 | 排在 CC 连接 / 公司选择（D 步）之后；CC 部分随下一版 CC 一起发 | 和 D 步同时改 `company-cards.tsx`、`i18n.ts` 会冲突 |

## 13. 工作量与排期（机器耗时）

| 步 | 内容 | 写码 + 单测 | 机器跑 |
|---|---|---|---|
| W1 CC | `probe_warehouses`（ISTAB 21 + MAINLOC + 在用的仓）进心跳目录；契约测试；62 套离线回放 | 约 1 小时 | Companion checks 约 15 分钟；测试机 runner |
| W2 共用层 | 仓库判定纯函数、`location` 记忆 kind、映射器写行 `loccod` | 约 2 小时 | 全闸门约 20 分钟 + 生产镜像候选离线检查约 40 分钟 |
| W3 Cowork 服务端 | `reported_warehouses` 收存、DocType `kind` 加 `location`（原生迁移）、开始前确认卡片出仓、推送前校验、公司规则垫底 | 约 2 小时 | 含在全闸门 |
| W4 Cowork 入口 | LINE 追问里问仓、复核页下拉与批量编辑、CC 卡片、公司卡记忆 | 约 2.5 小时 | 流程约 30 分钟 |
| W5 验证 | 流程用例、产品地图、冒烟；测试机 CC staging 实写读回 | 约 1.5 小时 | 测试机约 1 小时（主会话排） |

合计写码约 9 小时 + 机器跑约 2.5 小时。顺序：W1 可先单独做（旧服务端忽略新字段）；W2 → W3 → W4 → W5。部署：服务端先上（没有仓库表时什么都不变），CC 新版后发，发了以后多仓账套才出现仓库选择。


## 14. 施工记录（11-08，分支 `pearnly/cowork-express-warehouse`，基线 `origin/main` = `2ead204d5`）

按第 13 节 W1–W5 一次做完；第 12 节 7 条照建议。

| 步 | 落点 | 说明 |
|---|---|---|
| W1 CC | `cc/catalog_probe.py`（`probe_warehouses`、`_scan_stcrd`）、`companion/README.md`、`version.py` 1.12.18 | 目录多报 `warehouses`：ISTAB 21 全部仓 + 主仓；主仓与本账 STCRD 动库存的**进货 / 销货行（POSOPR 0 / 9）**用过的仓标 `"1"`（与记账指纹同一遍扫，同 10 万行上限；口径见下「测试机只读实测」）；STCRD 读坏了不带这个键（不报空表冒充「没有仓」）。写入不改 |
| W2 共用层 | `K/express_rules/push/choices.py`（`location` 类、`warehouses_in_use`、`main_warehouse`、`_locations`、`apply_to_payload` 写行 `loccod`）、`referenced.py`、`cc_desktop/wire/report_rules.py`（`WAREHOUSE_KEYS`）、`write_precheck/contracts.py`（`location` 归物料组） | 判定见第 6 节；只放非主仓（主仓不带仓码，载荷与今天逐字节相同）；`finalize_payload` 见行上 `loccod` 盖载荷 4。领取指纹只在载荷带了仓码时多一类 `warehouses`（已准备好的旧单指纹不变）。`ask_location` 只有人在场的入口给 |
| W3 Cowork 服务端 | `connections/{model,reports,preparation,service,api}.py`、`Cowork Express Choice` / `Cowork Company Choice` 的 `kind` 加 `location`（DocType 原生迁移）、`push/application/service.py`、`push/express/{decide,company_choices}.py`、`companies/{choices,context}.py`、`folder_intake/{contracts,push,briefs}.py` | 心跳存 `reported_warehouses`（旧版不报不写空表）；`save_choices` 只收在用的仓；行上人选的仓按存货对照的键记下（`remember_locations`）；公司规则可垫默认仓；新只读接口 `connections.api.warehouses`（复核页取在用的仓）。**定时任务推之前的判定不问仓**（`choices_view(ask_location=False)`），自动推（`assess` / `automation`）不问 |
| W4 入口 | 复核页 `intake-upload.js`（每行「仓库」、批量编辑「仓库」栏，只在多仓账套出现）、`targets.js`（「要选的项」弹窗的默认仓）、CC 开始前卡片（`precheck_source` 预选主仓）、CC 公司卡「记住的选择」（`company-cards.tsx`、`operator-shared.ts`、`i18n.ts`）、LINE（`line/items.py`、`push/application/line_items.py`） | LINE：有按行追问时末尾接一问仓（快捷回复列在用的仓、主仓在前），答案记成默认仓；没有要问的行不单为仓问 |
| W5 验证 | 新流程 `cowork.express.warehouse`（`scripts/flows/cowork_express_warehouse.py`）、产品地图两行补测试、单测 | 见下 |

文案：只新增「仓库 / Warehouse / คลังสินค้า / 倉庫」4 处（`cowork.choice.location` 与 CC 三处类名，沿用术语表现成的 Warehouse），不改已有文案（`check-copy.py --report`：新增 4、改动 0）。

**发版**：服务端随下一批部署（没有仓库表时什么都不变）；CC 部分（W1）随 **CC 1.12.18** 发，发了以后多仓账套才出现选仓。
