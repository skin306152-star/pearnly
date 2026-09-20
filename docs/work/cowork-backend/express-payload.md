# Express 预检与进销项载荷（第七批，本地纯后端）

本批已迁确定性的进项/销项完整载荷、预检顺序与准备结果。没有创建推送记录、队列或连接，没有执行 Companion、MR.ERP、内部 `/erp`、外部写入或部署。`ready` 只表示输入快照通过这些规则；不证明目标仍有权限、Companion 已连接或外部凭证已建立。

## 来源与归属

- 工作树：`/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，本批基线 `407ad132b2e69c871bcb272e2a7a361b6363d295`。
- 线上源码由主任务本批开工重新核实为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；固定参考目录 `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。交付前生产版本及配置复核由主任务记录。
- ERP 运行配置证据：`push-runtime-seventh-start.json`，SHA256 `111119d806e9daed01724baeec825453432078020717e21d484a4a7fe723573a`。这只是本批采集时的证据，不把 Companion 自报版本写成可执行文件已验证。
- 所属 App / 域：`pearnly_cowork.push.express`。无 DocType、RPC、数据库、环境读取、时钟读取、日志输出、密钥或第三方新依赖。
- 依赖方向：本包 → 已迁 `contracts.push` 声明规则、`targets.identity` 账套身份规则、`push.receipts.request_payload` 回执解包；不依赖生命周期或运行服务。把 `_d` 和日期解析拆入 `primitives.py`，解除旧 common/payment/bank 的环形依赖。

| 固定来源（相对旧仓） | 新落点 | 保留内容 |
| --- | --- | --- |
| `services/erp/express_push/{mapper,sales_mapper,common}.py` | 同名文件及 `primitives.py` | RR/HP、IV/HS，借贷/税额、货/费用、明细、付款、客户/供应商、新主档标志、CP874、V1版本与 ACK 常量 |
| `services/erp/express_push/{preflight,enqueue}.py` | `preflight.py`、`prepare.py` | 九项检查顺序、blocked/pending 原因、明细 mismatch/incomplete 人工分流；不移植入队回执 |
| `services/erp/express_push/{payment,bank_evidence,posting_profile,stock_acc_group,catalog_resolver}.py` | 同名纯模块 | 六层付款优先级、银行金额/方向/±7天、库存画像、存货科目组与目录匹配 |
| `services/erp/express_push/{doc_sanity,__init__,prior_doc}.py` | `doc_sanity.py`、`account_rules.py`、`prior_doc.py` | 单据防呆、端点账套规则、自动库存关闭、原凭证候选键与已授权结果附加 |
| `services/purchase/{field_clean,ocr_corrections,intake,item_verdict,totals}.py` | `field_clean.py`、`field_corrections.py`、`field_rules.py` | 真实使用的字段清理、日期/金额修正、货费与付款判据、7/107；不迁草稿创建和 SQL |
| `services/expense/{confidence,merchant}.py` | `confidence.py`、`merchant.py` | Express 原记账置信闸和数字商户白名单；不替换为 OCR L1 证据置信函数 |
| `services/erp/erp_payload.py`、`services/summary_import/columns.py` | `history.py`、`names.py` | 首个非重复原页扁平化、泰文目录名称骨架 |
| `services/erp/express_push/{direction,posting_kind}.py`、`internal_push_guard.py` | 复用既有 `contracts.push` | 明确声明优先、stock/service、内部 ERP 永不外推 |
| `services/erp/express_push/payload_keys.py` | `payload_keys.py` | 仅进销项 mapper 键集合；未宣称等于实际 Companion 接收白名单 |

## 公开纯接口

```python
from datetime import date
from pearnly_cowork.push.express.preflight import ExpressContext, preflight_express
from pearnly_cowork.push.express.prepare import prepare_express

context = ExpressContext(
    enabled=True,
    today=date(2026, 9, 21),
    own_tax_id=authorized_company_tax,
    tenant_id=authorized_tenant,
    mappings=authorized_mapping_snapshot,
    supplier_profile=authorized_supplier_profile,
    bank_index=authorized_bank_snapshot,
    prior_docnum=authorized_previous_erp_docnum,
)
result = prepare_express(endpoint_snapshot, history_snapshot, context=context,
                         posting_kind="service")
# disposition: ready / manual / disabled; reason、payload、preflight 供原生装配使用。
# 本调用没有 success、queued 或任何外部效果。
```

- `preflight_express(...) -> Preflight` 保留 `ready/blocked/disabled`、`checks_json()`、`reason/request_body/payload/direction/category/verdict`。完整发送准备应调用 `prepare_express`，因为原 enqueue 在预检之后还检查不平/残缺明细。
- `build_express_payload(history_flat, *, config, today, prior_docnum=None, mappings=None, category="", posting_kind=None)` 和 `build_express_sales_payload(...)` 可单独生成 `ExpressMapResult`。这些低层函数不承担来源/主体授权。
- `ExpressContext` 全部身份、配置和证据都必须由未来原生服务按当前 actor、tenant/client/company 与目标范围读出，不能接受客户端自报的 mappings、prior_docnum、银行或供应商档案。构造 context 不等于授权。
- `prior_history_id(history, fields)` 精确保留 `fields.history_id` 优先、其次本票 `id`；它返回不可信查找候选，不读取账本、不证明授权。回导原键授权必须沿已受理 Source/Batch 关系验真，并以原生 History/当前范围限制查询。不能拿这个 helper 的返回值直接查任意历史。当前没有 native 调用方。
- 新平台主体名称不要求旧 SQL 的整数 ID；context 由上层范围验证后提供，缺 history 主体锚时即使给了税号也保持 `subject_unbound`。

## 保持的细节和有意拆分

金额仍使用旧确定性 `Decimal` 规则和分位 `ROUND_HALF_EVEN`：总额平衡容差 0.02，明细对账容差 1.00，含税明细比例拆税并把余数落末行。半分 `2.675`、负数、0.01、超大值与非有限值都对照来源；本批没有顺手改金额舍入、阈值或拒绝原因。源 wire 的固定税率数字 7.0/0.0 保持 JSON 数值类型，库存画像计数比例仍为 float；金额计算没有改用 float。

- 付款顺序：人工 → 显式付款字段 → 真实票种 → 供应商档案 → 银行匹配 → 未声明。`vat_checkbox` 合成票种跳过真实票种付款推断。销项不会消费供应商档案。
- `stock_lane_enabled(config)` 恒 False，只限制**画像自动选择库存**；显式批次 `posting_kind="stock"` 或逐行 stock 仍能产生库存载荷。源进项混合明细是否附 stock_acccod 的首行规则保持，未擅自改成任意行匹配。
- account_set 默认端点兼容、上报 writable 账套/根目录匹配、未上报科目表跳过、脏 payload version 值按现有版本处理都保留。它们不替代 `targets` 中目标证据和冻结规则。
- `confidence.grade` 异常时旧预检返回 None 放行的行为保留；未把一次纯迁移变成新的置信策略。未来原生服务必须提供有效类型的授权快照。
- 日期由 caller 显式注入；生产实现不读 `date.today()`。旧供应商/银行/原凭证 SQL 和环境标志改成显式依赖，不模拟数据查询失败为“查过且没有”。原件输入、配置和 context 证据在预检内深拷贝，准备操作不污染调用者对象。
- `prepare_express` 保留来源检查先于 feature flag，包括停用时内部 `erp_web/line_erp` 也返回 `erp.internal_only`。旧 enqueue 的 `EXPRESS_QUEUED/HTTP202` 只由未来真实入队层生成；本包只报告 `ready`。
- 旧 WRITE_PAYLOAD_KEYS 不含预检后追加的 `account_root/account_dir/account_company/account_set_row`。这里保留源补键但不宣称已核对真实 Companion。会计手录收付等其它载荷未纳入本包。

## 验证

2026-09-21，本任务工作树候选：

- 170 条离线 unittest 通过、无 skip：149 条原纯断言按新 import / 显式日期 / prior IO 注入适配，21 条新契约测试。保留源业务断言；明确未纳入旧 MRERP 友好文案跨模块测试 1 条和实际 Companion 跨仓接收测试 1 条，二者不记作通过。
- 固定来源差分 1,013 组，28 个来源文件逐一 SHA256 绑定：预检 261、进项 261、销项 261、最终准备 87、金额边界 44、字段修正 8、画像 16、存货科目组 15、目录 6、付款 54。完整结果、原因与载荷相等，异常边界亦精确比较，不放宽金额断言。
- 差分执行选中旧纯 AST；依赖口上的公司税号、主档、配置、银行、原凭证号与日期来自合成快照，不加载旧服务框架、不连接数据库、不读取凭据或环境、不出网。所有非金额组若抛异常直接失败，避免两边同样坏掉误算通过。
- `--source` 和 `--source-stdin` 两条读取路径通过；对源文本追加字符后 hash gate 拒绝，退出非零。
- `python3 scripts/check-architecture.py`：PASS；`git diff --check`：PASS。锁定候选 Python 3.14 镜像上的最终无网络全套由主任务统一执行并登记。

复现（Python 3.14 候选；开发机可用已有运行时）：

```sh
PYTHONPATH=apps/pearnly_cowork:tests python -m unittest discover -s tests -p 'test_cowork_express_*.py' -q
PYTHONPATH=apps/pearnly_cowork:tests python scripts/cowork-express-source-parity.py --source /path/to/fixed-source
python3 scripts/check-architecture.py
```

Colima 不共享宿主机 `/tmp`，不可把空挂载误认为已提供源码。隔离容器使用 `--source-stdin`，由宿主 Python 按脚本 `SOURCE_HASHES` 键读取固定无密源码、通过 subprocess.input 发送 `JSON(relativePath -> sourceText)`；哈希校验不变。

## 后续真实服务仍需完成

原生目标/主档快照装配、实时 actor/范围和功能授权、Source/Batch 原键授权、本站唯一 Push Log 原子保留、幂等/写者锁、lease/ACK、重试/unknown 恢复、Companion 真实接收契约及安装身份核验、外部写后确认都没有在本包完成。没有发起真实推送，没有合并/推送/部署，也没有代替用户业务验收。
