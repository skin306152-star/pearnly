# 第五十一批：识别分类规则、公司科目树与供应商记忆

开工 `de0db45e8085855462a9bb33c7ecc320497f04f8`，本地 Cowork 专属树。
开工生产回读 `baseline-fifty-first-start.json`，源码仍是 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。
代码 `10a67cf558b09b753eff361916f952bf40272d01`；本地实现/定向验证完成，未合并、未推送、未部署。

## 范围与归属

用户要求继续迁移、纯展示不要。继续有业务作用的部分：规则/模型分类词映射到本公司科目，
识别复用该供应商人工复核过的分类，结果写回票面 `fields.category`，供 Express 科目映射消费。
不新增 `category_tag`、分类统计、记忆数量、补全列表、供应商主档或前端。

App `pearnly_cowork`：纯规则在 `contracts/classification.py`；树、记忆及编排归
`history/categories.py`；专用管理 RPC `history.category_api`；DocType 在原生模块目录。
history 单向消费 contracts，不新增域间依赖；不引入旧采购域、模型调用或外部写入。

固定来源：`services/purchase/categories.py` 的预设/两级树；
`services/expense/category_ai.py` 的确定性规则；`services/purchase/image_category.py::_category_names`；
`services/ocr/recognize/category_tag.py` 的规则优先与记忆净化；
`services/clients/store.py` 的供应商记忆与 `routes/history_routes.py::history_update` 的学习；
`services/ocr/recognize/persist.py` 的识别时覆盖顺序。

## 本批只读调查发现

- 供应商主档在过账档案列表中的 LEFT JOIN 仅补 `supplier_name`；Express 实际读取仍走
  `get_profiles`，按税号找现/赊档案。按用户指示不为列表名称新增主档。
- M1 泰文名闸实际只在 `routes/workspace_routes.py` 的账套主体建/改入口，
  其名称兜底消费者是 `services/workorder/steps/sort.py`；不在普通买方 `/api/clients`。
  新平台 `Cowork Workspace` 对应账套，`Cowork Client` 对应买方，不能给所有买方强加泰文名。
  工单自动分拣未纳入本次 Cowork 范围，因此本批不迁该附属强制闸，不关闭任何已有身份/权限保护。
- 第四十九批的 `preview_client_import/commit_client_import` 实际落到买方 `Cowork Client`，
  而来源 `/api/workspace/clients/import/*` 是账套主体。原入口已交付“买方导入适配”，
  不能用它证明账套导入迁完；本批不静默改变已有 RPC 的落点，账套导入单独登记差异。

## 有意适配

- 记忆按 Site + 公司 + 买方范围隔离（原来是租户共享卖家名），避免从未授权客户记录借出记忆。
- GET 科目树零写入；识别入库与显式初始化 POST 才为空树种预设，用公司行锁串行化首次种子。
- 停用科目不再参与自动推荐；不硬删、不改历史票面/Express 已配置映射。旧规则可读停用项，
  此处显式改成停用即停止推荐。科目名称仍不是 ERP 总账科目号，两者由既有映射关联。
- 请求指纹固定原识别输入；重放命中已存记录后不再分类，人工复核不被自动分类覆盖。
- 仅首次有效主页学习；原始 `ai_raw` 不变。手工新增不自动套规则；分类学习与历史复核在同一事务。
- 相同票面重复保存不增加学习次数；只有实际改票面才学习。记忆不公开列表/计数。
- 科目通用 CRUD、列表、分享均拒绝，管理只走 master_data + Cowork Admin 的专用 RPC；
  管理变更用原生 Version 审计。识别/复核仍只需原 intake 范围，不要求额外购买 master_data。
- 模型词仅作为确定性规则未命中后的名称匹配输入，不为分类多调用模型。

## 入口和事务

前缀 `pearnly_cowork.history.category_api`。没有新菜单/页面，点击去向不适用。

| 入口 | 用途与契约 |
| --- | --- |
| `category_tree(workspace)` | 读取当前授权公司有效两级树，零写入；需要 intake/ocr_history/master_data 任一功能角色 |
| POST `initialize_categories(workspace)` | 空树种 16 父类共 89 节点；已有自建或停用树绝不重种；返回含停用节点 |
| POST `save_category(...)` | 新增用 request_key，更新用 name + expected_revision；最多两层，父级不可搬迁 |
| POST `disable_category(...)` | expected_revision 检查，停用父级级联子级，不改历史票面 |
| POST `set_category_enabled(...)` | 恢复/停用；子级恢复要求父级有效，恢复父级不自动恢复子级 |

写 RPC 只允许 master_data + Cowork Admin，服务端重验产品/功能/范围；拒绝跨公司父子、
无效输入、过期版本、原生忽略权限旗标与分享旁路。初始化/管理持有公司行锁。
只在这些无外部 IO 的独立 RPC 边界对 QueryDeadlockError 回滚并最多重跑 3 次；权限错误不重试。
内部 `history.storage._create` / `update_pages` 直接调服务，不能由 RPC 重试器回滚上层业务事务。
原识别 workflow 遇数据库冲突从冻结结果延后重试，不重做付费 OCR。

树/记忆普通失败按旧“尽力分类”语义回落并记录异常类型（不记录票面或秘密）；savepoint 回滚部分写入。
树读取失败保留模型分类，可用同范围记忆；只有记忆失败则保留规则结果。权限/死锁不吞掉。
这不是分类正确性保证，也不新增“分类成功”显示位。空有效树表示用户全部停用，不复活历史记忆。

## 验证与交接

隔离环境：context `colima-pearnly`，project `pearnly-cowork-backend-lab`，内部无外网网络，
bench 镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`；
源码只读挂本任务工作树。只写标记的本地合成 Site，不调用付费模型/LINE/ERP。

| 检查 | 实测 |
| --- | --- |
| 纯规则 unittest | 7 通过 |
| 固定线上源码 AST 子集差分 | 3,608 组、0 差异，含各规则别名与 16 父类预设 |
| 升级 Site `cowork-backend-ack.localhost` | 17 条通过：复核学习→识别沿用→纯 Express 科目映射、隔离/撤权、版本/幂等、停用恢复、失败回落、有限重试 |
| 新装 `cowork-backend-classification.localhost` | Frappe 原生安装 Core/Cowork 成功，16 条业务检查通过（之后新增的第 17 条 RPC 重试界限测试在 ack 通过） |
| 原生 HTTP | 两 Site 各 15 请求通过，5 个匿名 RPC 均 403，GET 写入口/通用 CRUD 拒绝 |
| 两进程首次初始化 | 从旧 RR 快照同时开始，最终仅 89 节点，无重复 |
| 受影响回归 | 重复提醒 13、识别集成 6、执行成功/账单重试/冻结结果恢复 3 通过；外部出口为假适配 |
| 原生升级两轮 | 原票/偏好摘要不变；第二轮科目/记忆/原票摘要均不变，含自建停用项和学习记录 |
| 静态 | architecture 0 违规；i18n-build 1,561 词/5 App 一致；diff --check 通过 |
| 翻译报告 | check-i18n REPORT-ONLY 425：较前批新增 48 是中文匹配别名，不是界面文本；既有翻译欠账保留 |

实际遇到并修复：并发首种时第二连接报 MariaDB 1020（RR 快照后当前读冲突），
原服务正确上抛但新独立 RPC 未承接重试；补 RPC 三次上限后真实并发复验通过。
未扩大架构/i18n 忽略基线。升级摘要与检查清单见 `evidence/fifty-first-local-validation.json`。

开工/交付/上一批源码身份比较均 PASS，源码仍 `ec2e5c9`。但推送端点配置摘要从
`d23570c1332deff1a646c69e9a18b921` 变为 `ce9d2473b7d79e9088a40661c0f4bbf4`，
其它身份字段未变；摘要涵盖整个端点 config（包括心跳/目录），具体字段未定位，不能断言只是心跳。
本批分类服务没有端点配置依赖，已测的是显式输入的纯 Express 分类映射，未验证实际外部推送。
下一次推送相关迁移必须先核对该漂移。只读采集未修改任何线上配置。

限制：无旧库科目/记忆回灌；供应商仍按名称而非税号匹配，沿旧规则截 seller 200/category 80；
科目改名不会自动改历史票面或 Express 字符串映射，可能需要重配，不能当 ERP 科目号同步。
没有前端/浏览器/真机或用户验收，ERP 前端与 DMS 业务未修改。

## 交接保留与下一步

本地 main 收尾回读 `466beae046b580bb7eab303893cf20bb9498cc4b`；远端 main
`ac33e0c7362915ae314295cb26f7748cb5589de0`；远端任务分支不存在。
代码提交时本任务对本地主线仍有 66 个独有提交，不能删除工作树/分支冒充清理完成。

| 资源 | 归属/保留原因/解除条件 |
| --- | --- |
| `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly` + `codex/cowork-backend-migration` | Cowork 后端迁移；用户要求继续本地，未合并。下批复核，最终获准整合并核实包含成果后清理 |
| project `pearnly-cowork-backend-lab` 的 bench/db/redis | 本任务；收尾均 exited，仍有本树只读挂载，下一批复用；迁移验收/数据保留安排明确后再删除 |
| `pearnly-cowork-backend-lab_db`、`pearnly-cowork-backend-lab_sites`、日志与 Redis 匿名卷（完整 ID 见证据） | 原生升级对照与合成数据；含本批新装 classification Site，不可只因容器停止就删。每批开工复核 |
| `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b` | 固定旧线上来源，后续差分仍需，最终完成来源复核/归档安排后清理 |
| 本树既有 `__pycache__`（apps、scripts、tests） | 早前批次生成，未逐项证明为本批新产物，保持不动；下次整体收工逐项核对 |

仅移除本批明确生成的 `classification.cpython-314.pyc` 与 `test_cowork_classification.cpython-314.pyc`，
均可由测试重新生成；未删除业务数据、分支、工作树或卷。未发现本树符号链接或仍运行的本批主机验证进程。
下一块处理**账套主体导入**；不要把现有买方 RPC 偷换落点。改挂/解绑仍待用户跨域决定。
