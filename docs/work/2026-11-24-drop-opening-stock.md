# 删除补期初路径（11-24，库存 K 后续窗口）

评估：`docs/work/2026-11-24-opening-stock-ob-评估.md`（分支 `pearnly/stock-opening-ob-assess`），主会话 11-24 批准直接删。
随 CC 桌面版 1.12.25 发；服务端先于 CC 部署。

## 依据
- 原生 Express 期初只记在主档 OPNBAL / OPNVAL 与批号 `B/F`，库存卡里没有期初行（NLTTEST 副本 272 件期初、339 行出库指向 B/F；C 盘 100 本 OB 行 0）。
- CC 补期初却另写一行 `OB{yymmdd}-{nnn}` 进货行，前缀不在 ISRUN，同 K2 会挡财务原生录单。
- 本仓没有任何代码写入 `opening_stock`（旧仓补期初卡没迁来）；生产只读（11-24，主会话审跑）：ccdemo、erpcowork、testcowork 三个站点
  单据记录与推送记录里带 `opening_stock` 的都是 0，CC 从没照写过 OB 行。

## 改了什么
- 服务端：`preflight_express` 不再把 `merged_fields.opening_stock` 透传进载荷；`payload_keys` 去掉这个键。
- CC：删 `stock_sale.set_opening_stock` / `_set_stmas_opening`、`dbf_schema.stcrd_opening_row`、`worker._opening_items` 与 `write_sale(opening_stock=…)`；
  旧服务端万一还带来非空 `opening_stock`，`write_sale` 整单回 `PAYLOAD_INVALID`、账套一字节不动（不悄悄丢掉期初照写）。
- 测试：删补期初的单测；服务端改成断言载荷不带 `opening_stock`；CC 加「带期初的销货整单拒收、账套不动」；
  `scripts/cowork-express-source-parity.py` 去掉 opening_stock 一例（有意变化）。

## 共用件连带回归（`scripts/check-impact.py`）
改到的登记共用件只有销货写入 `dbf_sales.write_sale`：删的是一个没有入口的可选分支，没带 `opening_stock` 的载荷走的代码一字未改。

| 入口 | 结果 |
|---|---|
| cc.desktop.write | 通过：companion 全套（含销货、库存销货、载荷闸） |
| erp.cc.write | 通过：同一写入函数，ERP 载荷从不带这个键；companion 全套覆盖 |
| cowork.express.push | 通过：服务端 preflight 单测（不透传）+ 闸门 |
| cowork.cc.channel、cowork.cc-chat.push | 没测到（原因：这两条只把载荷交给同一个 `write_sale`，载荷除这个键外不变；Cowork 推送没有自动冒烟，集成验收时推一张） |
| erp.cc.pos-day、erp.cc.pos-refund、erp.cc.stock-* 、cc.desktop.operator 等其余入口 | 没测到（原因：不经 `write_sale` 的期初分支，代码未变；随 1.12.25 集成验收） |

## 检查
- companion 全套、闸门、候选离线、public-scan：见交付消息。
- code-review（medium）：3 条，全修（见交付消息小表）。
