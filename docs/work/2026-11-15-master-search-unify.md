# 主档搜索统一与 LINE 选公司回显（2026-11-15）

分支 `pearnly/master-search-unify`（叠在 `pearnly/link-ledger-code-first` 之上，合入时后者在前）。

## 现象（11-15 真机 / 线上核对）
- LIFF 采购单供应商框按电话搜不到；电话要带横线才匹配。
- 网页收款弹窗按税号搜客户：服务端已返回，被官方 Autocomplete 按选项文字在本地筛掉。
- LINE 按税号 / 账套名打字选中公司后不回显选中的是哪家；提示语还写「输入公司名称关键字」。

## 改动
- 采购 / 销售单对方与物料框（`masters/selection.api_choices`）打字找、网页收款客户框、LINE 收款找客户，都改走选择框同一份查询
  `masters/link_search`（名称、编码、账本编码、税号、电话；本公司账本档在前）。
- 电话列按去掉 `- 空格 . ( )` 后的数字比（`search_rules.phone_digits / phone_column`），带不带横线都认。
- 收款客户选项的 description 放税号：原生 Autocomplete 本地按「标签 + 说明 + 值」筛，税号命中不再被筛掉，下拉第二行显示税号。
- LINE 打字直接选中（税号、账套目录名、编号、唯一名称）时，下一问第一行是选中的那家名称（数据，不是新文案）。

## 改已有文案（用户 11-15 已批「可以」，主会话转）

| 键 | 语言 | 原 | 新 |
|---|---|---|---|
| Type a word from the company name to find it → Type the company name, tax ID or account set to find it | zh | 输入公司名称关键字查找 | 输入公司名称、税号或账套名查找 |
| 同上 | th | พิมพ์คำในชื่อบริษัทเพื่อค้นหา | พิมพ์ชื่อบริษัท เลขประจำตัวผู้เสียภาษี หรือชุดบัญชีเพื่อค้นหา |
| 同上 | ja | 会社名の一部を入力して検索 | 会社名・納税者番号・会計データ名を入力して検索 |
| 同上 | en | Type a word from the company name to find it | Type the company name, tax ID or account set to find it |

用到的地方：LINE 采购 / 销售选公司（`integrations/line_flow.py`）、LINE 收款选公司（`integrations/line_collection.py`）。
