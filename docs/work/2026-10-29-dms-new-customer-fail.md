# 2026-10-29 DMS LINE 登记客户「写入 DMS 失败」调查

只读调查，未改代码、未改数据。个人信息（证号、电话、客户姓名）一律不写入本文。

## 结论

- 站点：`ris.pearnly.com`（操作员 `dmsop-abef1047`，接 EP-00001 等同一套 DMS）。
- 这位客户在 DMS 里**已经有一份同名旧档**：没有证号、没有客户编号、没有称谓（快速建客或早年导入留下的那类）。
  LINE 流程把它列成「相近客户」，操作员两条路都走不通：
  1. 点候选「1」认领（11:45、11:49 两次）→ 我们在发给 DMS 之前就拦下：
     `customer selprefix requires an explicit selection`（错误码 `ERR_DMS_MASTER_UNMATCHED`）。
     认领只带「证号 + 姓名」，旧档称谓是空的，而保存前的级联校验要求称谓、地址必须有显式值；
     称谓补上后，空地址还会接着拦（本地复现见下）。
  2. 点「ลูกค้าใหม่」新建（11:50，截图这次）→ DMS 拒收：
     `customer create failed: 200 'err::"ชื่อ นามสกุลลูกค้า" ซ้ำ'`，即 **DMS 不允许两个客户同名**。
     我们按证号复查找不到这个人（旧档没有证号），编号也不是被占用，于是按「拒收」报
     `ERR_DMS_CUSTOMER_SAVE`。
- 不是 fcb65cca（10-28）或 8fd0fa4d（10-29）带进来的：失败发生在 11:45–11:50（曼谷），8fd0fa4d 在 13:22 才部署；
  `git diff fcb65cca~1 8fd0fa4d` 对 `intake/ client/ contracts/ line/ snapshots/` 无改动。
  认领只带证号 + 姓名（`intake/flow.py` `write_update`，自 1866f54e 迁移起）、称谓/地址不猜不补（`intake/customer_geo.py`，照搬旧产品 501589f0）、
  同名拒收按普通拒收处理（`client/client_intake.py`，c5f9c540 起，`scripts/test-dms-customer-identity.py`
  `test_name_duplicate_and_prefilled_codes_are_unchanged` 还把它锁成现状）都是迁移时就有的行为。
- 用户看到的两句话都没有说出真实原因，操作员照着做也解决不了：
  - 认领失败那句（`ERR_DMS_MASTER_UNMATCHED`）：「รายการที่เลือกไม่อยู่ในข้อมูลหลักของ DMS แล้ว กรุณากลับไปเลือกใหม่」
    （所选项已不在 DMS 主档，请回去重选）——实际是旧档缺称谓/地址，LINE 里没有地方可选。
  - 新建失败那句（`ERR_DMS_CUSTOMER_SAVE`）：「บันทึกลูกค้าเข้า DMS ไม่สำเร็จ — ตรวจสอบข้อมูลแล้วลองใหม่」
    （请检查资料后重试）——实际是 DMS 已有同名客户，重试永远同样失败。

## 证据（生产只读，2026-10-29 06:45–07:00 UTC）

`tabPearnly Push Log`（ris，时间为站点时区曼谷）：

| 记录 | 时间 | 请求 | 结果 |
| --- | --- | --- | --- |
| PL-2026-00065 | 11:45:52 | `mode: overwrite`、`field_diffs: []`（=认领候选），两次证号尾 4 位相同 | failed `ERR_DMS_MASTER_UNMATCHED`，`customer selprefix requires an explicit selection` |
| PL-2026-00067 | 11:49:04 | 同上 | 同上 |
| CW-5e525a65… | 11:50:49 | 新建 | failed `ERR_DMS_CUSTOMER_SAVE`，`err::"ชื่อ นามสกุลลูกค้า" ซ้ำ`，`submitted: false, rejected: true` |

- 识别日志 `pearnly.dms.ocr_timing.log`：04:43:39、04:48:19、04:50:00（UTC）三次身份证识别都 completed，与三次尝试对应；识别本身没问题。
- 同一时段 `tabError Log` 无记录；`tabDMS ID Card Intake` 10-28 起无记录（LINE 流程不写这张表）。
- 容器已在 13:22 部署时重建，11:50 前的 docker 日志已不在；`bench.log`/`frappe.log` 该时段只有与本事无关的
  scheduler「Skipped queueing」。
- 9-20 起 ris 上同类失败只有：本次 3 条，加 10-05 一条 `ERR_DMS_CUSTOMER_EXISTS` 与一条 `ERR_DMS_CUSTOMER_SAVE`（另一客户，未追）。
  CW 新建记录 10-20 起 10 成功 1 失败（即本次）。
- 本地快照（EP-00001，完整客户组）：共 15417 位客户，**6613 位没有证号也没有编号**；抽其中 200 位，**200 位都没有称谓**。
  所以「认领无证号旧档」在 LINE 里基本都会被称谓拦下，不是这一位独有。
- 现状：这位客户的 DMS 档（内部号 6062）**现在已带上与这次识别相同尾号的证号、客户编号、称谓、三段地址**，
  而我们系统 11:50 之后没有成功写入记录 → 推断是 11:50 之后有人直接在 DMS 网页里补齐的（需问用户确认）。
  若属实，这位客户已不用再登记。

## 影响范围

- 触发条件：DMS 里已有**名字被识别名包含**的旧档（快照搜索是子串匹配），且旧档没有本次证号。
  - 旧档缺称谓（无证号旧档几乎全是）→ 认领必失败，提示误导。
  - 旧档名字与识别名**完全相同** → 新建也必失败（DMS 同名拒收），提示误导。两条同时成立时操作员在 LINE 里无路可走。
  - 名字只是相近而不相同 → 新建能成功，不受影响。
- 「没有相近客户」（直接新建）和「证号命中」两条路不受影响。
- 测试租户 testdms 没有复现（会写测试 DMS 且要用测试 LINE 账号走真机，本次未做）；改用下面的离线复现。

## 本地复现（离线，假 DMS，不登录、不写任何外部系统）

借 `scripts/test-dms-customer-identity.py` 的假 DMS，造一份「同名、无证号、无编号、无称谓、无地址」的旧档：

| 场景 | 结果 |
| --- | --- |
| 选「新客户」，DMS 回 `err::"ชื่อ นามสกุลลูกค้า" ซ้ำ` | `ERR_DMS_CUSTOMER_SAVE`，原文与生产一致 |
| 认领，只带证号 + 姓名（现行做法） | `ERR_DMS_MASTER_UNMATCHED` `customer selprefix requires an explicit selection`，未发写请求，与生产一致 |
| 认领时补上身份证称谓 | 接着报 `customer selprovinces requires an explicit selection` |
| 称谓 + 身份证地址都带上 | 假 DMS 的下拉选项不全，测不出（真实 DMS 需另验） |

## 建议修法（待主会话定，未施工）

一次到位按类处理「认领无证号旧档」，不针对这一位：

1. **认领时用身份证补旧档的空项**：认领（`ACT_PICK` → `write_update`）在旧档称谓、户籍地址为空时，带上识别草稿里已解析的称谓与地址
   （只补空项，已有值不动，口径同 `customer_merge.secondary_addresses`）；电话照现在规则。补不出（识别称谓/地址解析不到主档）
   才走浏览器编辑页让人选。这样同名旧档走认领就能写成，新建同名也就不需要了。
2. **DMS 同名拒收给准话**：`cus/new.php` 回 `"ชื่อ นามสกุลลูกค้า" ซ้ำ` 且查无本人时，单独给一个错误码，回复里说「DMS 已有同名客户，
   请选上面的客户」，并重新给出候选（而不是「检查资料后重试」）。
3. **称谓/地址缺值不再报「已不在主档」**：`_require_member` 空值时用单独的码与文案（缺称谓/地址，去编辑页补），
   与「所选项已从主档删除」分开。
4. 回归：上面四个离线场景写成测试；真实 DMS 上用测试租户造一份无证号同名旧档，走认领与新建各一次（测试账号）。

文案改动按仓库规矩先列清单给用户看。

## 待确认

- 6062 号客户 11:50 后是否由岳父或店员直接在 DMS 网页补齐（只问，不改数据）。
- 10-05 那条 `ERR_DMS_CUSTOMER_SAVE` 原因未追。
