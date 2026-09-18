# DMS 卡片文案进语言包：代码里一个泰文字符都不留

日期：2026-09-18。承接 [24-billing-base.md](24-billing-base.md)。
线上基准：`pearnly-app` 的 `origin/master` = **`501589f0`**（本批开工时核对过，未变）。

## 一、这一批做完的

`dms_cards.py` 的 57 个文案常量全部撤掉，改成按键取词（`_t("TXT_XXX")`），
正文进 `apps/pearnly_dms/pearnly_dms/translations/th.csv`（116 条）。
外部 20+ 处 `cards.TXT_XXX` 老写法由模块级 `__getattr__`（PEP 562）继续支持，
**调用方一行没动**。

语言包机制见 [i18n.py](../../apps/pearnly_dms/pearnly_dms/i18n.py) 顶部注释：
两个入口 `t(key)`（按会话语言）与 `t(key, lang=…)`（**按收件人**语言，
LINE 通知必须用这个 —— 对方是泰国销售，后台可能开着中文）。

⚠️ **不是所有泰文都该进包**，这条边界以后每次都要判：

| 位置 | 进不进包 | 为什么 |
|---|---|---|
| 卡片文案常量、前端可见中文 | ✅ 进 | 改文案不该改代码、发版 |
| `dms_commands.py` 里的 `เมนู` | ❌ 不进 | 那是**用户会打的词**，是匹配模式 |
| `dms_client_ops.py` 的 351 处泰文 | ❌ 不进 | 那是**外部 DMS 的字段名/ID**，翻了就写不进去 |
| `ACT_*` 动作码 | ❌ 绝不 | 是 postback 载荷，翻了按钮就点不动 |

## 二、顺手修掉的两个真 bug

1. **`_t()` 首参原名 `name`，与回执模板的 `name=`（客户姓名）撞名** ——
   调 `receipt_text()` 直接
   `TypeError: _t() got multiple values for argument 'name'`。
   写档成功回执照不出来。已改名 `key`。
2. **上一批搬文案时漏了两个键**（`BTN_EDIT_CANCEL` / `TXT_EDIT_PICK`）——
   编辑流程会把裸 `dms.card.BTN_EDIT_CANCEL` 显示给泰国销售看。已补。

为防第 2 类再犯，`scripts/sample-verify-dms-cards.py` 新增第 9 节：
扫**全 app** 的 `_t("X")` 与 `cards.TXT_X` 引用逐键比对语言包，
并断言四张卡的正文里不出现 `dms.card.`。这类漏键以后会自己报出来。

## 三、验证（34/34 通过）

`scripts/sample-verify-dms-cards.py` —— 通过 34 / 失败 0。
其中逐字比对项（改写前后文案必须一模一样，不能借重构改字）：
五要素标签、四张卡的卡头与 altText、写档回执（新建/更新/缺值）、
订车回执（有顾问/无顾问）、修改提示。

## 四、⚠️ 被这一批翻出来的"过期形态"（**没改，留给第 8 批**）

新平台现有的 `edit_menu_message` / `edit_prompt` / `EDIT_FIELDS` 这套
**「选字段 → 输入新值」的逐字段文字编辑，线上已经整体退役了**：

```python
# 线上 services/line_dms/customer_edit_entry.py 第 1 行
"""Open the browser editor; retired text-edit events cannot change customer fields."""
EDIT_ACTIONS = frozenset({"edit", "edit_field", "edit_cancel"})
# 注释原文：Only redirect historical cards; no field selection or text application remains.
```

线上点 `[แก้ไข]` 现在是**直接开浏览器编辑页**（`cards._edit_button()`：
基础按钮还是 `_btn(BTN_EDIT, _data(ACT_EDIT, nonce=nonce), "secondary")`，
`edit_link.url()` 有值就把 action 换成 `uri` 深链）。新平台这套目前
**没有任何调用方**（死代码），本批只让它在被替换前不自相矛盾。

**第 8 批要做**：`customer_edit` + `customer_edit_entry` + `edit_link`，
并把 `sample-verify-dms-cards.py` 第 8 节一起换成「编辑按钮产出 URI 深链」的验证。

## 五、其余已被确认的缺口（不是本批引入的）

- **菜单卡层还没建**：`TXT_MENU_*` 的键在包里备好了，但新平台没有任何地方
  构造菜单卡（`menu_card()` 不存在）。属第 6 批（菜单层 + Rich Menu + 登录票据）。
- **卡片上的 `[แก้ไข]` 是死的**：`handle_postback` 只认 保存/沿用/认领/重来，
  点了会消耗掉 nonce 再回"已过期"，用户以为是自己超时。同属第 8 批。

## 六、剩余批次（按依赖顺序，不能挑着搬）

```
masters_cache → master_shared → 租约锁        ✅ 已完成（db647df）
advisor → masters_cache                      ← 下一批
booking → payload / readback / submit / org
```

| # | 内容 | 状态 |
|---|---|---|
| 1 | `dms_cards.py` 语言包化 | ✅ 本批 |
| 2 | `dms_advisor.py` 返工（依赖 master_shared） | ✅ [26](26-dms-advisor-parity.md) |
| 3 | 客户写入链：`client_ops` / `client_forms` / `company_banks` / `docno` / `intake` | |
| 4 | 订车执行链：`booking_org` / `booking_payload` / `booking_readback` / `booking_submit` / `booking_customer` / `booking_errors` | |
| 5 | 订车逐问（线上 9 个文件 ~2800 行）：`booking_qa*` / `qa_cards` / `qa_util` / `text_fields` | |
| 6 | 菜单层 + Rich Menu + 登录票据 | |
| 7 | 查询流：`query_flow` / `query_cards` / `query_access` | |
| 8 | 字段修正：`customer_edit` / `customer_edit_entry` / `edit_link` | |
| 9 | 改写审批：`approval_flow` / `approval_store` / `approval_cards` | |
| — | **多 OA 那套不搬** | 新平台"一站一租户一条 LINE"已等效实现 `channel_key` |

> 为什么旧项目文件这么碎：那边有铁律 `scripts/check_file_size.py` #27.1 ——
> **任何文件超 500 行 = push 失败**。这是旧项目的约束，**不照搬到新平台**，
> 新平台按职责分领域包。

## 七、开工前的固定动作（踩过坑）

```bash
REF=$(cat /tmp/pearnly-live-ref-path)   # /tmp/pearnly-legacy-live.uVn2Kr
cat /tmp/pearnly-live-sha               # 501589f0d7426fff2c24584d55e2732e68961063
cd /Users/skin/Developer/Pearnly/pearnly-app && git fetch origin master -q \
  && git rev-parse --short origin/master   # 变了就要重建参考副本
```

**只读参考副本才是权威；本机那份 `pearnly-app` 停在下线状态（`e4505459`），
落后线上 125 个 commit。** 代码里的注释（例如 `dms_flow.py` 顶部
"3 处未搬"）同样是旧状态，**不能当清单用，要读线上代码**。
