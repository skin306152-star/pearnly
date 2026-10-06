# DMS 地址近似匹配 + 菜单 1、2 的非主档卡片改成文案加按钮（2026-10-06）

用户：「菜单1和菜单2……除了客户主档和订车主档有专用的卡片，不要出现卡片，其它都是文案加按钮的方式，而且这个文案要官方一点……然后把匹配机制做了……做好验证后等我指示，先别合并」；
补充：「新客户卡，差异卡，编辑后的卡，订车卡，订车编辑后的卡保持不动，其它都是文案加按钮」，资料一致卡、订车里的客户确认卡、菜单卡也不动。
分支 `pearnly/dms-geo-near-match`，基线 `origin/main` 8b085440。**未合并、未部署**，等用户指示。

## 现场

testdms 真机 LINE 实测（样卡 ต.หนองขมาร）：识别读成 หนองชมาร，镇按名字精确匹配失败，邮编也补不上；差异卡只列门牌/หมู่/府/县，
点「更新资料」后被写入前校验拦下，回「客户地址不完整」卡片让销售进编辑页。

## 改动

| 行为 | 入口 |
| --- | --- |
| 府、县、镇在同一父级里没有精确匹配时，接受**唯一**一个只差一个字的名字；名字不足 4 个字、多个候选、差两字以上一律留空；邮编不做近似（仍是"镇确定后唯一邮编自动补"） | `contracts/geo_matching.near`、`resolve` |
| 点「编辑」后 / 地址不完整：文案 + 原生快捷按钮「แก้ไข」（能拿到深链就直接打开编辑页） | `line/cards.edit_entry_message`（原 `edit_entry_card`） |
| 相似客户候选：文案逐行列出（序号 · 客户码 · 姓名 · 证号尾 4），快捷按钮「序号. 客户码」+「ลูกค้าใหม่」 | `line/cards.candidates_message`（原 `candidates_card`） |
| 订车建单失败重试：文案 + 快捷按钮「ลองใหม่」「ทิ้ง」（原按钮「ลองสร้างใบจองอีกครั้ง」21 字，超过 LINE 快捷按钮 20 字上限） | `booking/conversation/qa_cards.booking_retry_message`（原 `booking_retry_card`） |
| 文案改正式：地址不完整、相似候选、订车重试两条提示；新增「点编辑」提示 `dms.card.TXT_EDIT_ENTRY` | `i18n/messages.tsv`（四语） |

保持卡片：新客户卡、差异卡、资料一致卡、编辑页保存后的客户卡、订车里的客户确认卡、订车预览卡（含编辑后）、菜单卡；菜单 5 查询卡不在本次范围。

## 验证（未部署）

- 本地：`development.yml` 全部 Python 闸门、`check-dms-offline.py` 23 项、新增 `tests/test_dms_geo_near_match.py`（8 条，已登记）通过。
- 生产 DMS 容器隔离目录 `/tmp/pfix` 跑分支代码（跑完已删）：`test-dms-intake-queue.py` 23 项 OK（两条用例改为断言快捷按钮）；
  `sample-verify-dms-cards.py` 53/0、`sample-verify-dms-qa-cards.py` 27/0（testdms 站点上下文，只构造消息不发送）。
- 还缺：部署后 LINE 真机重走同一张样卡；快捷按钮在手机上的样子由用户确认。

## 菜单 6 教程视频：ris 漏配 → 改为 DMS bench 共用（同日追加）

- 现象：ris 操作员点菜单 6 看不了，用户（LINE 绑在 testdms）能看。原因：视频地址按站点写在 site_config，10-02 上线步骤是"先 testdms、验收后再写正式站点"，ris 一直没写，按设计回「教程视频暂未上线」。
- 处置（用户同意）：ris 站点配置写入三项（值取自 testdms），回读三项都能生成可播放的视频消息；DMS bench `common_site_config.json` 也写入同三项（原有键不变），现有与以后新开的 DMS 租户都直接生效。
- 代码：`scripts/production/configure-dms-runtime.py`（每次 DMS 激活都会跑）用 `setdefault` 写这三项，生成的地址与线上逐字一致；站点或 bench 已有自己的值不覆盖。`test-dms-runtime.py` 加 2 条并登记进 `development.yml`。
