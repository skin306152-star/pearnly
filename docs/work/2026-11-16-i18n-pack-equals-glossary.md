# 翻译包 = 术语表（2026-11-16）

分支 `pearnly/i18n-pack-equals-glossary`，基线 origin/main `9e0e102ab`。主会话 11-16 派：引导式第 1 期改文案后四语翻译包各残留旧写法，
靠人工清才让文案闸归零；生成器 `scripts/i18n-build.py` 原先「合并保留手工行」，术语表改了旧行删不掉。

**不赶下一次部署**（主会话 11-16）：动全部 App 的翻译包；Cowork 翻译包内容变了，对账存档代码指纹会变
（`recon_center/view_store._code_fingerprint`），部署后存档重算一次（10-02 类似一次约 490 CPU 秒）。部署说明要写明。

## 盘点（基线 9e0e102ab）
术语表外的翻译包行，每种语言约 770 行。判定「在用」的范围：本仓全部受控文件（DocType / 报表 / 工作区 / 打印格式 JSON、
fixtures、hooks、Python、Desk JS、frontend/、Keycloak 主题、tests、scripts；不含 docs/ 与翻译包、术语表本身），按原文、去转义后
原文、带占位符的按各段；按 key 取词的另认前缀拼接（`t(\`dms.err.${code}\`)`、`"dms.card." + name` 等）与常量名；Frappe / ERPNext
（线上镜像 02eebaa4 里的源码与它们的 .po / 翻译包）里有这个 msgid 的算覆盖自带文案、在用。拿不准一律按在用。

| App | 搬进术语表（在用） | 删除（没人用） |
|---|---|---|
| pearnly_control | 15 | 13 |
| pearnly_core | 31 | 35 |
| pearnly_cowork | 118 | 125 |
| pearnly_dms | 145 | 75 |
| pearnly_erp_portal | 99 | 106 |

## 两个提交
- **A（搬行）**：在用手工行原样追加进各 App `i18n/messages.tsv`（四语照现有译文，一字不改；英文包缺行的无键行英文即 msgid）。
  搬进来的带 key 词条登记在各 App `i18n/no-alias.txt`：生成器不按它们的英文补别名行（Frappe 把各 App 译文合成一份字典，补了会改掉
  Save、Bank、Count 这类全站通用词的显示）。生成器仍是合并模式。文案闸门把「基线翻译包手工行、键与四语逐字节相同地搬进术语表」认作
  移动（`copygate/diff._adopted`，改一个字照新增查，有测试），规则阈值不动、不新增豁免。
- **B（严格化 + 删行）**：生成器只写术语表（加别名行），`--check` 遇到术语表外的行报错；重新生成即删掉没人用的行。回退 B 即恢复。

## 实际生效字典比对
按 Frappe 16.33 `translate.get_translation_dict_from_file` 的读法（`\n` 还原、去首尾空白）导出各 App 四语字典，逐键比：

- 改前 → A：删 0、改 0；新增 4（英文包：control `Not permitted`、`Reference`，core `Failure reason`、`Note`，值即 msgid 本身，
  原先英文包没有这一行，`_()` 本来就回 msgid，显示不变）。
- A → B：删 1415（各 App 各语言合计），全部在「没人用」清单内；新增 0、改 0。

## 文案闸门（check-copy --staged，提交 A）
新增 38 · 改动 0 · 删除 0 · 译文改动 0 · 移动 370 · 拦截 0 · 标黄 0。38 条「新增」是文字原样、但英文已是基线术语表里某个带 key 词条的
英文（闸门不把它们当手工行），按新增查后无命中。搬进来的老译文若按新增查会命中拦截 60、标黄 134（78 个键），清单在共享笔记
「老文案整改清单-11-16.md」，这次不改，另起一件。

## 验证
见主会话汇报（全闸门、候选离线、流程）。
