# 更正：语言集是**四种**，不给 `zh_TW` 单独建包

日期：2026-09-18。承接 [31-docno-and-client-ops-parity.md](31-docno-and-client-ops-parity.md)。

## 我错在哪

搬文案时我看到老产品 `services/erp/dms_booking_errors.py` 的话术字典键里有
`zh_TW`，就把语言集定成了 `th / en / zh / zh_TW / ja` **五种**，还给它建了一整套
`translations/zh_TW.csv`。

**这是把"数据结构里出现过"当成了"产品要维护"。** 老产品自己的
`scripts/check_i18n.py` 第 9 行写得很清楚：

> 是 5 种语言 (zh / en / th / ja · **zh_TW 通过 fallback 到 zh**)。

即：正规语言集是 **中 / 英 / 泰 / 日四种**，`zh_TW` 只是**回落读中文**。这也与产品
界面的语言开关一致（登录页上就是 中 / TH / EN / 日）。

多建一套包的害处不是"多几个文件"：某个词会被单独在繁体里改掉，两边慢慢就不一致，
而实际上根本没人维护那一档。

## 改了什么

| 改动 | 内容 |
|---|---|
| `i18n.LANGUAGES` | `("th", "en", "zh", "ja")` |
| `translations/zh_TW.csv` | **删除** |
| `i18n.pack()` | 带地区码、又没有自己的包 → **回落基语言**（`zh_TW` → `zh`），不为此多维护几套译文 |
| 注释与文档里的"五种语言" | 改成四种，并写明 `zh_TW` 回落的出处（`check_i18n.py`） |

回落是通用写法（去掉 `_`/`-` 之后的地区码），不是给 `zh_TW` 开的特例。

## 验证

`scripts/sample-verify-dms-intake.sh dms` 从 26 → **29**，新增三条把口径钉死：

| 用例 | 锁住的规则 |
|---|---|
| 语言集就是四种（不多不少） | `("th", "en", "zh", "ja")` |
| 没有为 `zh_TW` 单独建包 | 防止再被"扩展"回去 |
| `zh_TW` 回落读中文（同一个词拿到同一句） | 回落真的生效，不是拿到裸 key |

全套 273 项全绿（cards 34、advisor 25、intake 29、draft 21、client 77、
booking-foundation 45、booking-readback 42）。

## 一句自查

这一类错误的共同形状是：**把线上某个数据结构的取值范围，当成产品决策照搬。**
本批次之前也犯过同形的错（把 `dms_flow.py` 顶部注释里的"3 处未搬"当清单用）。
以后遇到"照搬"，先找线上**说明这个集合本身**的地方（比如 `check_i18n.py`），
而不是从某个字典的键反推。
