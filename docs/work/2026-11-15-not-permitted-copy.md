# 「没有权限」文案统一（2026-11-15）

## 现象

同一个英文原文在几个 App 的术语表里译文不同，泰文、日文尤其如此。站点装了几个 App 时，取哪一份要看 App 的顺序，同一句拒绝在不同页面显示得不一样。

## 用户确认（2026-11-15，经主会话转达，原话「可以」）

两种大小写的原文统一用 Frappe 官方译法：没有权限 / ไม่ได้รับอนุญาต / 権限がありません（Frappe 没有日文包，日文取最短的那种）。术语表只在 pearnly_core 里各留一行，cowork 与 erp 里分叉的几行删掉。代码不动。

| 原文 | 改前 | 改后 |
|---|---|---|
| Not Permitted（pearnly_core） | 没有权限 / ไม่มีสิทธิ์ / 権限がありません | 没有权限 / ไม่ได้รับอนุญาต / 権限がありません |
| Not permitted（pearnly_core 新增一行） | 共用层原来没有 | 没有权限 / ไม่ได้รับอนุญาต / 権限がありません |
| Not Permitted（pearnly_cowork） | 没有权限 / ไม่ได้รับอนุญาต / 適切な権限がありません | 删除（用共用层的） |
| Not permitted（pearnly_cowork） | 没有权限 / ไม่มีสิทธิ์ / 権限がありません | 删除（用共用层的） |
| Not permitted（pearnly_erp_portal） | 没有权限 / ไม่ได้รับอนุญาต / 権限がありません | 删除（用共用层的） |
| This account cannot send documents（pearnly_erp_portal） | 中文：该账号无权发送单据 | 中文：此账号无权发送单据（与 Cowork 一致；泰文、日文不变） |

## 文案闸门清单（`python3 scripts/check-copy.py --report` 第 1 节）

| 来源·键 | 中文 旧 → 新 | English | ไทย | 日本語 |
|---|---|---|---|---|
| pearnly_erp_portal·This account cannot send documents | 该账号无权发送单据 → 此账号无权发送单据 | This account cannot send documents | บัญชีนี้ไม่มีสิทธิ์ส่งเอกสาร | このアカウントは書類を送信できません |

译文改动（中文未变）：pearnly_core·Not Permitted 的泰文 ไม่มีสิทธิ์ → ไม่ได้รับอนุญาต。

## 影响

只改术语表与生成的翻译包，代码不动。泰文、日文页面上原来显示「ไม่มีสิทธิ์」「適切な権限がありません」的拒绝提示会改变，中文不变。
