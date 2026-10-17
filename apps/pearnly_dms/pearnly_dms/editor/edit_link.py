# -*- coding: utf-8 -*-
"""把 LINE 用户带回本站点页面（编辑草稿）的**深链**。

## 为什么要有它

LINE 里只能点按钮，改不了表单。所以卡片上的 `[แก้ไข]`（编辑）得把用户带到本站点
的页面去改，改完再回来。这个链接就是那个入口。

## 与老产品的差异（**有意**，因为架构不同）

老产品是**多 OA 一个库**：一个 LIFF 服务好几个官方号，所以要按 `channel_key` 去
渠道注册表里查，还要处理"这个 OA 没有自己的 LIFF 就借共享 Provider 的"。

新平台是**一站一租户一条 LINE**：本站点就是那一个渠道。所以：

| | 老产品 | 新平台 |
|---|---|---|
| LIFF 从哪来 | 渠道注册表 `channels.liff_id(key)` | 站点配置 `pearnly_line_liff_id`（开通时写入） |
| 链接里的 `channel` 参数 | 有（多 OA 要区分） | **不需要**（一个站点只有一个渠道） |
| 没有 LIFF 时 | 只有默认 OA 回落到网页路径 | 一律回落到本站点网页路径 |

⚠️ 没配 LIFF **不是错误**：调用方据此**省略按钮**，绝不能给一个打不开的链接
（点不动比没有更糟）。

## 链接契约（浏览器编辑页那一批要照这个读）

```
https://liff.line.me/<liff_id>?draft=<nonce>[&editor=customer]
<站点地址>/liff/dms-booking?draft=<nonce>[&editor=customer]
```

`draft` 是草稿 nonce；`editor=customer` 表示按**客户档**编辑器打开（不带则是订车）。
"""

from __future__ import annotations

import urllib.parse

import frappe


def url(nonce: str, *, customer: bool = False, review: bool = False) -> str:
    """深链；取不到 LIFF 就用本站点地址。**两者都取不到才返回空串。**

    `customer=True` → 客户档编辑器（老产品点 `[แก้ไข]` 走的就是这个）。
    `review=True` → 开单回执差异卡的修改页（`draft` 是"投递记录.卡片确认号"，见 `review_fix`）。
    """
    editor = "review" if review else ("customer" if customer else "")
    query = urllib.parse.urlencode({"draft": str(nonce or ""), **({"editor": editor} if editor else {})})
    from pearnly_core import line

    liff = line.liff_id()
    if liff:
        return f"https://liff.line.me/{liff}?{query}"
    base = (frappe.utils.get_url() or "").rstrip("/")
    return f"{base}/liff/dms-booking?{query}" if base else ""
