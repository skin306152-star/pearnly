# Cowork 后端迁移：原生银行对账任务存储

日期：2026-09-21。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `abea988`（第二十批交付）。

本批落地第一个原生持久化切片：把已解码的流水与总账行存成本站原生记录，并运行已迁移的匹配核心，产出结果行与汇总。不接前端、不部署、不调用模型、不写 ERP。

## 来源清点：为什么只做银行对账这一代

旧项目里银行对账存在两代并存，本批先用消费点证据定形，再动手：

| 证据 | 内容 |
| --- | --- |
| 当前对账中心外壳 | `src/home/recon-center-x.ts`（2026-06-14 重设计，注释写明“覆盖旧 loadReconcilePage”）三类型共用外壳：`bank` 是流水对总账，提交 `/api/recon/bank-v2/submit`；`income` 是总账对 VAT，提交 `/api/recon/gl-vat/submit`；`tax` 是 `/api/vat_excel/submit`，也就是你已划掉的 Excel 公式内测 |
| 老会话加发票候选 | `/api/bank-recon/*`（upload/sessions/match/override/candidates）加 `services/recon/bank_reconcile.py`。旧仓 2026-05-18 调研已把它定性为“一整套老体系”，2026-06 清理报告又写明“不是死代码、前端还在用”，并建议迁到 `bank_recon_v2`，但当时未拍板，至今未完成 |
| 会计侧银行对账 | `services/accounting/{bank_recon,bank_candidates,bank_match}.py` 与 `/api/accounting/bank/*`（会过账出凭证）。属会计模块，按 `docs/p0/04-scope-and-drop-list.md` 已确认不迁移，本批不碰 |

采用结论：新平台的 Cowork 对账只承接**银行对账（流水对总账）**这一代，任务表用 `reconciliation_type` 预留 `income`；老 v1 会话与发票候选不建原生表，旧数据留在旧库只读；会计侧那条不在范围内。这条判断有来源证据，不是猜的。若你要保留发票候选流程，那是另加一对 DocType，不是重做本批。

## 实际落地

| 落点 | 内容 |
| --- | --- |
| `reconciliation/bank_recon_contracts.py`（纯） | 已解码行的校验与规范化：金额用 `Decimal` 精确校验（非负、最多 6 位小数、量级上限、`float()` 可表示），日期 ISO，描述必填且不超过 300 字，未知字段拒绝；页眉四类金额加银行码与科目码；请求指纹为规范化载荷的 SHA-256；另提供转匹配契约的转换（含“余额未读到则构造后置 None”这一来源约束） |
| `reconciliation/bank_recon_documents.py` | 原生控制器基类：命名、服务专属写能力（`flags.bank_recon_write` 对象身份，JSON flags 无效）、记录标识与工作区不可改 |
| `reconciliation/bank_recon_access.py` | 权限：`recon` 购买功能、`Cowork Bank Reconciliation` 或 `Cowork Admin` 角色、已启用用户、工作区公司范围；`has_permission` 拒绝通用删除与分享；查询条件按工作区授权过滤，并拒绝旧 DocShare 扩大可见范围 |
| `reconciliation/bank_recon_service.py` | `create`（校验、匹配、同事务写任务与结果行）、`records`、`detail`、`delete`（先删明细再删任务，属服务专属） |
| `reconciliation/bank_recon_api.py` | 白名单 RPC：`create` 与 `delete` 仅 POST，只对本地死锁 1020 重试；`records` 与 `detail` 为 GET |
| DocType `Cowork Bank Reconciliation`、`Cowork Bank Reconciliation Row` | 任务（含汇总字段与请求键/指纹，`request_key` 唯一）与结果行（两侧字段齐备，`task` 建索引） |

行字段语义：`match_layer=0` 表示未匹配；`date_diff_days=-1` 表示不适用；`stmt_balance_known=0` 表示余额列未读到，对应匹配契约的 `None`；`stmt_balance_ok` 空值表示无法核对；`stmt_autocorrected` 汇总来源的金额或方向自动修正标记。金额列存 `Float`，因为部署的匹配与余额链契约本身就是二进制浮点（第十九、二十批已登记），存储入口先用 `Decimal` 精确校验再进入该契约。

幂等：任务名是 `CBR-` 加 `sha256(site|user|request_id)` 前 40 位；同请求键同内容返回原任务并标记 `replayed`，同请求键不同内容抛冲突，不覆盖也不重复插行。

角色供给沿用既有做法：仓库不建 Role fixtures，`Cowork Bank Reconciliation` 角色由部署或验证脚本确保存在（与 `Cowork Stocktake` 一致）。这是部署步骤，不是本批遗漏。

## 验证

隔离环境为 Docker context `colima-pearnly`、project `pearnly-cowork-backend-lab`，源码只读挂载、Internal 网络、无发布端口。

```sh
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T bench bench --site cowork-backend-ack.localhost migrate
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T bench bench --site cowork-backend-preparation.localhost migrate
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T -e PEARNLY_COWORK_TEST_SITE=cowork-backend-ack.localhost bench bash -lc 'cd /home/frappe/frappe-bench/sites && PEARNLY_COWORK_BACKEND_LAB=1 ../env/bin/python /workspace/scripts/cowork-bank-recon-lab-verify.py'
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest tests.test_cowork_bank_recon_contracts
python3 scripts/check-architecture.py
```

- 隔离 Site 的 **8** 个用例在**两个既有 Site**（`cowork-backend-ack.localhost`、`cowork-backend-preparation.localhost`）各自通过：汇总与四类计数、结果行状态与排序、完整回放不重复、同键不同内容冲突、未读余额往返、工作区范围与购买功能与角色拒绝、撤销授权后读写拒绝、通用删除与 DocShare 拒绝、服务删除清空明细；匿名 WSGI 三个入口均返回 **403**。
- 迁移：两个既有 Site 的 `migrate` 通过，并重复 `migrate` 一次仍通过，无错误无残留。
- 离线：新增 **15** 个契约用例通过（金额精度、量级、负值、未知字段、日期、枚举、页眉、指纹稳定性与易变性、转换含未读余额）；既有对账 **102** 个用例继续通过；架构 **0 违规**，`git diff --check` 通过。
- 顺带修掉同任务遗留的红灯：`tests/test_cowork_push_purity.py` 未登记第十五批新增的 `push/export_actions.py`（纯）与 `push/export_facts.py`（依赖 frappe）。现按测试本意分类登记，两个用例转绿，没有放宽任何断言。

未执行：全新 Site 安装（只在两个既有 Site 上迁移）、异步任务编排、真实 XLSX/PDF 解析与模型抽取、导出与人工复核动作、前端接入、部署、外部业务结果与用户验收。宽范围 `test_cowork_*.py` 仍有 24 个模块因本机缺 `frappe` 与 `openpyxl` 无法导入，属既有环境限制，与本批改动无关。

## 运行投影

本批窗口内首尾比较为 `settings_unchanged: true, observations_changed: false`；跨批仍会变化。完整配置与旧状态迁入继续 HOLD。

## 下一步

同一任务模型可继续承接：受控解析适配（把旧 XLSX/PDF 解析结果落成同样的解码行）、异步任务状态与轮询、导出复核工作簿、人工复核动作；收入对账（总账对 VAT）复用 `reconciliation_type=income`。老 v1 会话数据与会计侧银行对账不在迁移范围。
