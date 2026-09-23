# 第五十三批：银行 PDF 逐行复核与确认后重对账

2026-09-22。用户最新范围：此前列出的 1–4 不做，只继续第 5 项；收尾、同步和验收随前端一起做。
本批仅实现后端及必要本地验证，不开前端、不合并、不推送、不部署、不调用付费模型。

## 明确不做，不再留作可选待办

1. 识别记录跨公司/买方的改挂、解绑；现有不可变归属和“仍有票据则拒绝删买方”继续保留。
2. 银行/GL 额外图片、Word、pdfplumber/坐标表格读取器。不是取消普通发票的图片 OCR。
3. 多文件合并 `merge_statements` / `merge_gl_files`。
4. 列映射记忆、`needs_mapping`、确认列对应。

纯展示分类、WeKan 等之前已明确裁剪的范围继续不迁。真实付费调用、Windows/ERP 接线验收、
历史数据与运行配置导入、旧词库欠账、整合主线及最终部署均留到前端/同步阶段；没有代签验收。

## 来源、落点与依赖

开工基线 `6191f29811ddf1d830beaffb6f664a65bf9805b5`；本任务工作树与分支不变。
线上固定源仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；首尾重新采集且与第五十二批交付一致。
证据为 `evidence/baseline-fifty-third-{start,delivery}.json`，摘要分别为
`290798bc85ce911e09c8e8bb0d03b2f2deb6209b663ba2c23c47d8bdf3a23a31` /
`dce8475d99db94ec4b95cdcf04017e3a3ad215e5e40c1d5bfa50387140db2b9d`。

| 固定源文件 | SHA-256 | 新落点 |
| --- | --- | --- |
| `services/importer/stmt_review.py` | `cd28ed22e9d121a1870c070ebd63e60f99f9f24c067c07f4fc31fc75969ec589` | `reconciliation/bank_review.py`：纯触发条件、编辑契约与余额链 |
| `services/recon_jobs/bank_handler.py` | `3d33ac2d3bdaccbc0d7071bb50fbbc503ca21d477e0f7d83dc5aa076e0d46b63` | `bank_recon_service.create_from_documents/confirm_review`：停止、快照、重对账 |
| `routes/recon_jobs_routes.py` | `2f05944f00c8bcdc0cc2fd897f0c86be815a97d83a4c345a78b65cec2fea1063` | `bank_recon_api.review/check_review/confirm_review` |

所属 `pearnly_cowork` / `reconciliation` 域；原生数据仍是 `Cowork Bank Reconciliation` 和结果行。
只增加隐藏只读 Long Text `review_json` 与 `needs_review` 状态，无新 App、域依赖、菜单或页面。
文件/OCR 仍由 DocType 编排层交出数据；纯复核模块不依赖 Frappe/IO。
配对算法不改；原有配对及存结果行代码各抽成同域小函数供确认复用，没有移动公开路径。

## 实际行为与原生适配差异

- 单份 PDF 已解析成功且有行时，低置信度行、机器改过金额或完整性失败任一命中，则任务停在
  `needs_review`。仅余额警告本身不额外触发 S8，符合固定源谓词。表格/CSV 和显式解码行入口不新增流程。
- 待复核没有配对结果行，不允许导出空的“完成报告”。列表、详情及创建响应返回任务 `status`。
  `parsed.needs_review` 仍是旧解析质量诊断，可能对未进入复核流程的表格为真；不能拿它代替任务状态。
- 保存的是**原始解析结果**（已包含读取器自动修正）及诊断，不冒称保存了每次模型修正前的原始读数。
  快照包括 statement/GL 行、金额锚点、文件名、逐行置信度、余额检查标记、机器修正标记及完整性问题。
- 编辑范围是日期、摘要、支出、存入、余额。`idx` 指向原始行；省略某行表示删行，新增行用 `idx:null`。
  这样可以人工补漏行；不是多文件合并。未知字段、重复/越界编号、无效日期、负支出/存入、
  缺余额或超过两位小数拒绝。不能由客户端改写模型元数据、GL、公司或权限。
- 服务器以 Decimal 核对 `上行实际余额 + 存入 − 支出 = 当前余额`，容差 0.02；每行承接实际余额，
  一处错误不向后连带标红。确认也强制执行，不能靠客户端勾选跳过。
  有意不复制旧路由未执行服务端余额检查、错误值回落 0 或将 `balance_ok` 无条件置真的弱行为。
- 旧实现复制暂存 GL 再建新 job；本仓原生适配为**同一任务** `needs_review → completed`，
  revision `1 → 2`，使用冻结 GL 重对账，不复制文件、不重复解析、不再次调用模型。
  没有新增任意已完成任务“重跑”或审批系统；已完成结果需另发新创建请求才能重新读文件。
- 原始快照不覆盖；`confirmation` 留修改后行、操作者、时间、余额检查和请求摘要。
  期末余额默认取修改后的末行，已显式指定的期末锚点覆盖继续生效；原完整性诊断保留，
  人工确认不声称重新证明原文件所有印刷总数正确。
- 同内容确认重放返回同任务，改内容/错误版本拒绝。金额等值格式归一到两位小数。
  任务锁使确认与单删/批删串行；原有授权删除仍可删除整条任务及其审计，不引入永久保留政策。
- 原生任务角色仍为 Cowork Bank Reconciliation / Cowork Admin，继承 Site、recon 功能、用户有效及
  公司范围限制；不照搬旧 Flask 权限命名。每次读取、确认及重放重验，通用写/分享仍不能绕过服务令牌。
  文件创建入口增加前置权限检查，未授权者在读文件/解析/模型之前拒绝。

## 前端以后接的接口

前缀 `pearnly_cowork.reconciliation.bank_recon_api`，原生 Frappe `message` 封套：

| API | 方法 | 内容 |
| --- | --- | --- |
| `review(task_id)` | GET | name/status/revision、原始可编辑行、opening、逐行诊断、已有 confirmation |
| `check_review(task_id,rows)` | POST | 零写入；revision、逐行 checks、整体 ok；可反复检查 |
| `confirm_review(task_id,rows,expected_revision)` | POST | 首次传 JSON 整数 1；重试仍传原 1；完成后返回 revision 2 |

`rows` 为完整列表，每行只含 `idx/date/description/withdrawal/deposit/balance`；钱建议传十进制字符串。
读取确认后的任务仍返回原始 `rows`，已确认修改在 `confirmation.rows`；结果在已有 detail/export。
业务错误走原生 ValidationError；不会返回空结果伪装成功。本批补齐新增复核错误的四语文案，
不顺手处理旧词库欠账或触碰 ERP/共用词库。

## 本地验证与保留

验证记录见 `evidence/fifty-third-local-validation.json`。真实外部业务和前端验收仍未做。
隔离环境仅 `colima-pearnly` / `pearnly-cowork-backend-lab`，internal 网络、无监听、无 worker/scheduler、
任务源码只读挂载；Frappe 16.33.0 与锁定镜像不升级。
新装 `cowork-backend-bank-review.localhost`；既有 `cowork-backend-ack.localhost` 原生迁移两轮，
第一轮核对旧银行记录及此前主数据/识别证据，第二轮包括本批待复核及已确认任务的逐表摘要保留。

完成本地保存后停止三个 lab 容器，工作树/分支、固定源副本、lab Sites/数据库卷保留供前端整合复用。
清理/同步/最终验收按用户要求延后，解除条件为前端接入后明确执行整合与收尾；不删除未合并成果。
未合 main、未推送、未部署；另一窗口的 ERP 主线不属于本批。
