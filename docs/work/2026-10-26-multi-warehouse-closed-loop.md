# 一公司多仓库闭环 + 盘点就地新建：施工记录

方案与口径：[需求影响评估](2026-10-26-multi-warehouse-closed-loop-评估.md)（12 节主会话定的 8 条；13 节缺口、修法与分期）。
分支 `pearnly/multi-warehouse-p0`，基线 `origin/main` 1dabbd06（测试包 e 合入后）；评估、8 条口径与原生样本（调拨 RL、行备注，
`2025-10-25-express-native-rules.md` 4.6b / 4.9）三个提交从 `pearnly/stock-count-unknown-assess` 带进来。

## P0 卫生项

| 缺口 | 改法 | 用例 |
|---|---|---|
| G13 差异报表「只看」失效 | 「只看」「图表按」两个 Select 改官方 `{value, label}`：交给服务端的值固定（counted / uncounted / differences），显示跟界面语言；按类全仓查：脚本报表只有这一张，盘点工作台的「只看」已是 value / label，新建盘点的「方式」只在前端比较、没交服务端 | `tests/test-report-select-values.cjs`（进 CI：脚本报表的 Select 筛选不许拿译文当值；修前版本被拦） |
| G16 停用仓不查库存 | `warehouses.update_warehouse` 停用前按库位口径（`locations._stock_at`）查结存，有货拒「有库存的仓库不能停用」；不放进 DocType validate——Express 主档同步会自动停掉 Express 已没有的仓，不能被拦 | `test_erp_locations`：有货拒、没货照停、只改名不核 |
| G16 收银台可绑停用仓 | `ERP POS Terminal.validate`：新建或换仓时仓必须启用，拒「该仓库已停用」；仓后来停用的旧收银台不换仓照样能存 | `test_erp_locations`：启用仓过、停用仓拒、新建 / 换仓拒、没换仓照存 |
| G17 按台期初 Excel 找仓跨公司 | `serials.parse_workbook` 不再按仓名跨公司先找一遍（两家都叫 Main 时取到别家的仓、随后被拒），仓名原样交给开按台时的 `warehouses.required`，在本公司里按名称或编号认 | `test_erp_serials`：解析期初表不跨公司查仓（修前失败） |
| G17 打开按台跨公司汇总 | 主会话 10-26 定：数据口径不改（按台是商品级开关，共用商品各公司都有账，开之前全部公司都要登记到台），只改提示并守公司隔离——开不了时原生 msgprint 逐个列看得到的「仓 · 公司：已登记 / 账面」，看不到的公司合成一句「其他公司还有未登记的库存」，不露名称与数量；打开按台前的各仓账面表、期初模板同样只列看得到的仓（原来账面表露别家数量、模板写别家仓名）。按公司开按台记为后续项 | `test_erp_serials`：提示只列看得到的仓、别家合成一句；账面表别家合成一行不带数量 |
| G15 推送遇无码仓 | 采购 / 销售（`push.document`）、收银日汇总（`pos_day`）、出入库单与盘点（`stock_entry.document / count_document`）列出库存行上 Express 里没有仓库码的仓；`push.send` 在写账规则准备好之后（主档已读、仓库码已同步）停下交人 `express.warehouse_unmapped`（「仓库在 Express 里没有对应的仓库」），不再不带码、由 CC 桌面版静默写进 Express 主仓；主档还没读到时照旧先等读。处理：Express 建好同名仓、主档同步认领后「再写一次」，或手工处理后标记已处理；P1 建仓选 Express 仓后此路径变少 | `test_erp_express_direct`：`contracts.unmapped`；主档未读先等读、读到后停在 warehouse_unmapped、不排写账件、不交给共用规则；出入库单无码仓列出 |

新文案（新增 4 条，`check-copy` 过闸）：有库存的仓库不能停用 / 该仓库已停用 / 仓库在 Express 里没有对应的仓库 / 其他公司还有未登记的库存。

协调（主会话 10-26）：代录窗口（`pearnly/express-operator`）同期改 `express/push.py` 的 `send`（抽出「准备不入队」）。P0 先过闸、验收、合入，
代录窗口之后变基，把 `unmapped_warehouses` 那个停下交人的分支挪进它抽出来的函数；代录的单同样受「仓库没有 Express 码就停」约束，不能绕过。

## 暂停点（10-26 晚，用户指示全部窗口就地暂停）

- P0 六项代码与用例都已写完并提交在本分支（G13、G16 两项、G17 两项、G15），本地用例全过；闸门（132 项）跑在 G17 提示改动之前的内容上，
  候选离线检查跑在混合内容上——**都要在最终内容上重跑**。没推 main、没交验收、测试机没占用。
- **明天第一步**：在最终内容上重跑本地同名闸门（`~/pearnly-devtools/gate.sh`）+ 候选离线检查 → 推分支等 Development checks →
  实验栈实测 P0（差异报表「只看」三种值、停用有货的仓被拒、收银台绑停用仓被拒、按台期初 Excel 同名仓、打开按台的提示与账面表、
  无码仓的采购 / 出入库 / 收银日汇总停在 warehouse_unmapped）→ `check-impact` 受影响入口实测 → 交验收（只看差异）→ 合入 main →
  告诉代录窗口变基（把停下交人分支挪进它抽出的函数）。之后开 P1。
