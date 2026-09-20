# Cowork 外部推送纯预检合同

日期：2026-09-20。工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`，分支 `codex/cowork-backend-migration`，初始基线 `1d7832e6c5c767b310542b9022750992ec9957b0`。

本包新增 `apps/pearnly_cowork/pearnly_cowork/contracts/push.py`、`tests/test_cowork_push_contract.py` 与本文。只有纯语义、输入预检和识别结果副本处理，没有 API、目标读取、目标授权、队列消费或外部推送。不把预检正常返回称为“连接可用”或“推送成功”。

## 固定来源

使用父任务已经现场核验的旧生产源码 SHA `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`：`/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。对应 web/worker 100% 流量 revision `*ec2e5c957e6b-s3`、digest `178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`、GitHub run `35321281376`，在线二次回读由父任务记录。

| 固定源码 | SHA-256 |
| --- | --- |
| `services/erp/express_push/direction.py` | `808be2cb59be1f5270ae3e0d5b8aa16915b7603c2c8448a7df339dd80630ec8a` |
| `services/erp/express_push/posting_kind.py` | `4742aec35acfdcc04741300d57eb1fce0c7e9184f09d81767bc3df21e8cc0efa` |
| `services/erp/internal_push_guard.py` | `b64ba33ae6a1dd8fda9319aaf485b0280125c6aff2b17b42c058f963a356f14c` |
| `services/cowork_line/document_preflight.py` | `e5e4d38db837059c913263c0fa2afe2d0fc5f9ab9efd8d754f675e6b43777f6f` |
| `services/purchase/field_clean.py`（只用 `clean_tax_id`） | `43912e65fcce3afa53fa4f7cdfd2d57afc02ff5556c5e17a6d76c2d030bdbed8` |

## App、数据与依赖边界

所属 `pearnly_cowork.contracts`，只依赖标准库 `copy/re/typing`。参数来自将来运行层已授权的当前 Site、公司、用户、目标及原生识别记录；纯函数不确立这些身份，也不查询 ERP 产品数据库。不会把会计、工单、旧部署方式或自动外部写入带入新 App。

`PushContractError.code` 保持旧机器错误码，替代旧 FastAPI `HTTPException`/预检字典的运输绑定。将来的 Frappe API 负责本地化及错误状态映射；其中 `erp.internal_only` 原为 HTTP 403。普通返回仅表示**该项纯条件没有拒绝**，没有 `ok/ready/pushed` 对外成功字段。

## 迁入函数与保持的语义

| 新函数 | 保持的来源行为 |
| --- | --- |
| `normalize_direction` | `sales/income → sales`、`purchase/expense → purchase`；未知值和银行 `deposit/withdrawal` 为 `None`，不默认采购 |
| `explicit_direction` | `flat.direction`、`history.direction`、`flat.fields.direction` 依次选第一个真值后只规范化一次；无效但非空的高优先级值不跳到更低声明 |
| `clean_tax_id`、`detect_by_tax` | 原规则是剥非数字后恰好 13 位；自己的税号只命中卖方为销项、只命中买方为进项；双命中、都不命中或缺失为 `None`。没有新增 Mod-11 要求 |
| `resolve_direction` | 已声明方向优先，否则税号判断；税号来源必须是运行层已核实的当前公司 |
| `apply_batch_direction` | 原地写入已规范化的批级方向；不覆盖已有非空逐行决定 |
| `normalize_posting_kind`、`resolve_posting_kind` | 只认字符串 `service/stock`；显式有效声明优先，其次票记录，最后 `None`；不引账套级默认，不把未声明当服务 |
| `item_posting_kinds` | 无任何行声明返回 `None`；一旦任一行声明，所有行必须有效，否则返回 `[]`；完整声明返回逐行值。保留 `None` 与 `[]` 区别 |
| `require_external_history`、`require_external_user` | 记录来源 `erp_web/line_erp` 或调用用户入口 `erp` 独立拒绝 `erp.internal_only`；换门户不能让内部 ERP 记录外推 |
| `require_document_choices` | LINE 单据预检只接受明确 `purchase/sales`，不接受 `income/expense` 推断别名；付款仅 `cash/credit/None`；非法分别 `direction_required/payment_invalid` |
| `require_history_workspace` | `workspace_required`、`history_not_found`、`history_workspace_mismatch` 顺序保留；原记录公司 ID 与转换后的整数 ID 严格比较，未偷偷把存储字符串当整数 |
| `require_adapter_choices` | Express 必须显式传 `stock/service`，缺值 `posting_kind_required`、坏值 `posting_kind_invalid`；MRERP 付款缺失 `payment_required`，现金采购继续 `mrerp_purchase_cash_unverified`；其他适配器 `adapter_not_supported` |
| `with_manual_fields` | 深复制原识别记录后，将已验证方向/付款写入首个非重复非副本页；保留来源字段和原始输入；无主页面时按旧规则补一个，付款 `None` 不抹掉原有手动声明 |

注意几个容易混淆的边界：

- 自动判向允许历史别名，但 LINE 人工确认的预检不接受这些别名；两个函数承担不同合同，不能合成一个宽松门禁。
- 普通过账去向解析允许回退票记录；LINE Express 单据预检要求本次显式选定，不能用解析器回退静默绕过必填。
- `reconciliation` 中税号标准化用于核对字段，允许任意数字长度；推送判向的 `clean_tax_id` 必须恰好 13 位，不能因为名称相近合并为较宽松规则。
- `require_history_workspace` 保留旧 `workspace_client_id` 的整数语义。新原生 Link/name 的适配必须由运行层明确映射，不能直接以来源整数代替 Frappe 文档名。
- 已通知父任务将 `contracts/history.py` 的同名过账去向标准化改为显式复用本包函数；本包遵守文件归属，没有自行修改其他 Agent 的文件。

## 运行层仍必须完成

原 `preflight_document` 顺序是：方向/付款 → 重新读取并授权目标 → 公司必填 → 受用户/租户约束读历史 → 历史公司一致 → 当前主体匹配 → 适配器预检。纯合同拆出后，运行层仍须保持这个顺序和真实读取，不能只调用最后一个函数。

尚未迁入：

- 目标和当前成员重新鉴权、撤权、连接公司与单据公司区分；来源 `require_target/_express_endpoint` 的真实授权查询。
- 当前票面主体与公司匹配；没有把 `subject_ok=True` 写进产品代码。
- Express 特性、账套白名单、协议版本、连接可用性、方向开关、单据类型、币种/贷项/押金/日期防呆、映射、科目白名单与置信检查。
- MRERP/Express 载荷构建、原生推送日志、写者互斥、Companion lease/ack、网络未知状态恢复和外部结果回读。
- 所有金额相关 mapper、舍入和比较。本包没有引入金额 `float`，也没有为规避差异偷偷改变金额规则。

因此不能把这些函数作为完整推送消费者或业务闭环交付。

## 验证

在本工作树运行，Python 3.14，无容器、无网络、无数据库、无付费调用：

```sh
PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_push_contract.py' -v
PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_push_contract.py --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b
python3 scripts/check-architecture.py
git diff --check
```

- **19 个单测通过**：显式方向优先、税号歧义、未声明不推断、逐行声明完整性、内部来源拒绝、严格人工选择、公司范围、Express 必填、MRERP 现金采购拒绝、原始 OCR 结果不被修改。
- 固定源 SHA-256、随机 seed `20260920`：**34** 组规范化、**289** 组过账优先级、**1500** 组方向、**1500** 组行级去向、**8** 组内部来源、**3000** 组单据预检、**30** 组原文复制差分通过。
- 源差分只执行选定函数 AST；源文档预检的目标/主体/连接依赖注入测试替身，省去两个局部运行层 import，不修改 `sys.path/sys.modules`。所以该差分证明纯判断、顺序和错误码相同，**不证明真实权限、连接、外部推送通过**。
- 架构检查 **0 违规**，`git diff --check`、新增两个 Python 文件的语法/空白检查与纯依赖检查通过；交付前重读架构要求，未增加框架、IO 或反向依赖。
- Site、HTTP、真实权限/并发、前端、LINE 真机、部署、外部业务结果与用户验收：未执行。
