# 编辑深链：把 LINE 用户带回本站点改草稿

日期：2026-09-19。承接 [39-qa-cards-text-prepared.md](39-qa-cards-text-prepared.md)。
线上基准 `501589f0`。

## 一、为什么先做它

它挡在 `qa_cards` 前面：预览卡上的 `[แก้ไข]`（编辑）按钮就是这条深链
（`qa_cards._edit_url` / `_uri_btn` 调它）。不先把它落地，`qa_cards` 搬到一半会卡住。

## 二、老产品为什么那么绕，新平台为什么不用

老产品是**多 OA 一个库**：一个 LIFF 服务好几个官方号，所以查渠道注册表、处理
"这个 OA 没有自己的 LIFF 就借共享 Provider 的"、回落到默认 OA 的网页路径。

新平台是**一站一租户一条 LINE** —— 本站点就是那一个渠道：

| | 老产品 | 新平台 |
|---|---|---|
| LIFF 从哪来 | 渠道注册表 `channels.liff_id(key)` | **站点配置 `pearnly_line_liff_id`**（开通时由 `sample-provision.sh` 写入） |
| 链接里的 `channel` 参数 | 有 | **去掉**（一个站点只有一个渠道，没得区分） |
| 没有 LIFF 时 | 只有默认 OA 回落到网页路径 | 一律回落到本站点网页路径 |

所以这不是"照着改几个 import"，而是**按新架构砍掉多 OA 那层**。LIFF 的读取方式
沿用站点里既有的那套（`pearnly_core.line._conf("liff_id")`，与 `channel_id()` 同一写法）。

## 三、链接契约（浏览器编辑页那一批要照这个读）

```
https://liff.line.me/<liff_id>?draft=<nonce>[&editor=customer]
<站点地址>/liff/dms-booking?draft=<nonce>[&editor=customer]
```

- `draft` = 草稿 nonce；
- `editor=customer` = 按**客户档**编辑器打开（不带则是订车编辑器）。

⚠️ **没配 LIFF 不是错误**：一律回落到本站点网页路径。两者都取不到才返回空串 ——
调用方据此**省略按钮**（点不动的按钮比没有更糟）。

## 四、验证（5/5）

```bash
scripts/sample-verify-dms-edit-link.sh dms one761900.localhost   # 合计: 通过 5 / 失败 0
```

这个脚本要**站点上下文**（读站点配置与站点地址），所以包装脚本走 `bench console`，
用法是 `<组> <站点>`（与其它那些裸 python 的脚本不同，这里在用法里写明）。

| 用例 | 锁住的规则 |
|---|---|
| 配了 LIFF → `liff.line.me/...?draft=...` | 走 LIFF |
| `customer=True` → 带 `editor=customer` | 客户档编辑器 |
| **不带** `channel` 参数 | 多 OA 那层确实砍掉了 |
| 没配 LIFF → 回落本站点网页路径 | 不是报错、也不是空链接 |

全套 **350** 项：cards 43、advisor 25、intake 33、draft 21、client 77、
booking-foundation 45、booking-readback 42、booking-payments 42、booking-ledger 17、
edit-link 5。

## 五、下一批

`qa_cards` 的代码侧（现在包是现成的、深链也有了）：36 个构造函数搬过来、
文案改成按键取词、错误话术按码取用、把"防漏键"守卫扩到 `qa.*` 这一族。
搬完它，`qa_payment_cards` / `booking_qa_start` / `booking_qa_pages` /
`booking_qa_payment` 那几个小件才是能逐个落地的小片。
