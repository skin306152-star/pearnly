# 第十五批：复核表读取原生回执事实

基线 `95bdaf96ad32b567a25ed3b70a95ee968eaf73f0`，工作树和分支仍为 `cowork-backend-migration`。本包只完成原生回执到既有四表复核工作簿的读取，不增加发送、公开 API、DocType 或前端，不改 DMS/Core，不部署、不合并、不推送。

用户本轮明确允许 Windows 联调及使用旧项目测试凭据/测试账套写入第三方 ERP。这是后续必要联调的有效授权，不再沿用旧批次“无测试外写授权”的描述；本包为只读导出，不需要实际写 ERP，也没有切换现用客户端。

## 线上来源与配置差异

开工和交付重新采集了 `evidence/*fifteenth-{start,delivery}.json`。两次源码来源均为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；Web/Worker 实际100%流量 revision 分别为 `pearnly-web-ec2e5c957e6b-s3` / `pearnly-worker-ec2e5c957e6b-s3`；镜像 digest 仍为 `sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226`。source 比较通过。

第十四交付→本批开工，以及本批首尾的完整 runtime 比较均未通过：完整端点 config 集合摘要发生变化。原证据没有逐 key 的旧摘要，不能从旧 hash 还原变化字段，不能把它归因为 Windows 开机/心跳。原始失败证据保留；补充只读采集器以原 SQL 比较第七批 v1 身份：开工为 unchanged=true；交付补采因两次组件读取期间身份投影不一致而以 `component_database_identity_changed` 拒绝生成证据。此失败单独留档，未重试到通过或降低校验；交付 v1 等价不能由开工结果代替。完整 runtime 首尾差分只见端点配置摘要，其余已采投影字段相等。结果及每个 evidence hash 见验证清单。**完整配置迁入 HOLD 仍在**，不导入旧 owner/路径/账套/凭据或业务记录。

本包的迁移来源为固定线上导出函数和查询语义；没有依赖环境开关或导入当前生产端点的业务配置。新读取只作用于新平台原生账本及其有来源的本地报告。全 config 等价、真实 Windows 来源和业务结果不能由这次局部导出对比推出。

以下五份源文件均逐字对比了固定 Git blob 与只读缓存，hash 登记在验证清单：

| 固定旧源 | 保留行为 | 新落点 |
| --- | --- | --- |
| `services/erp/export_actions.py` | 按当前 actor/History 取创建时间最新的 success，独立于最近一次推送状态；seq 排序、真实 Express 单号、party/item code、新建与复用 | `push.export_facts` / 纯 `push.export_actions` |
| `routes/ocr_export_routes.py` | 从服务端事实装配工作簿，保留新建主档清单与最后成功单号；无授权事实不假装成功 | `roundtrip.service`，权限沿新平台公司/客户和连接规则 |
| `services/erp/express_push/stock_acc_group.py` | 从冻结载荷 stock_acccod 及报告候选显示组码/科目号/名 | 复用既有 `push.express.stock_acc_group`，`connections.service.stock_groups_for_log` 提供有来源的候选 |
| `services/excel/erp_workbook.py` / `excel_template_th.py` | 四表、固定泰文协议、显示状态与隐藏回导键、新建主档去重 | 既有 `roundtrip.export_mapping/export_writer`，本包未改模板与金额公式 |

## 权限、入口与一致性

所属 App 为 `pearnly_cowork`。单向依赖 `roundtrip.service → push.export_facts → connections.service`，纯解析只依赖既有纯 receipts/stock_acc_group，无 Frappe/IO 或反向导入。连接与报告数据归 connections；账本事实归 push；工作簿格式归 roundtrip。

唯一产品入口仍是 `POST pearnly_cowork.roundtrip.api.export(names)`，无新增按钮或菜单。先校验1至100个 History 全部可读、同公司/客户及原件绑定；服务器状态保留第十四批 History 投影。附加 ERP 事实只取本站当前 actor 的账本，并逐连接验证当前 push_log 购买功能、Cowork Push Log/Admin、公司/客户及显式连接授予。

- History 阅读权限不自动授予 ERP 明细；没有日志权限或已撤销连接授予时，仍可导出票据和既有 History 状态，省略 ERP 单号/动作/失败原因。停用连接不抹去仍获授权的历史。Admin 也不继承别的 actor 的导出事实。
- 最新 success 按旧源的创建时间选取；后创建的请求先完成、早创建请求晚完成，不按完成时间倒置结果。候选准备记录不覆盖先前成功。无权读最新成功时不回退到更早记录。
- 当前人工选择的 `last_push_record` 提供可授权的失败原因，最新状态沿第十四批 History 投影。状态与最后成功回执是两个事实：后续 manual 不删除前次成功单号，也不把失败响应中的票号当成功证明。
- 票面自报状态、ERP 单号和新建标记仍先清除。输出只含白名单事实；原始响应、目标、冻结载荷、凭据及未知 meta 均不输出。
- 库存组描述须与原回执账号/root/报告 credential/generation/issuer 的 cache origin 一致；不把另一来源的科目名套给旧组码，无匹配描述只显示冻结组码。不读取或解密外部凭据。

### 明确的原生适配与收紧

1. 旧 `user_id`/RLS 改为本站当前 actor + History 公司/客户 + 当前连接/log授权；沿用新平台已实现的权限边界，不扩展成管理员全员导出。
2. History 审核版本改变后，保留历史 ERP 单号/对手方码和组码，但不覆盖新票面方向、不套用旧逐行动作或生成基于新票面的旧主档清单。旧源直接按当前行位置套旧标记，这里避免编辑后的行错配。
3. malformed seq 只丢弃整组行映射，保留单号和单据级事实，不把剩余商品码前移；新建标记严格要求 bool，不把字符串 `false` 或数字视为建档证据。正常 boolean/seq 线协议保持旧源结果。
4. 不加入按票号反查的歧义 fallback，不吞掉数据库故障；仅预期权限拒绝省略附加事实。没有旧状态/旧日志迁入。

## 本地验证

8个定向真实 Site 用例最终整次通过；3个新纯解析用例、3个既有工作簿用例通过，共14个独立用例。另10组固定源码 AST 函数同输入比较通过，未导入旧生产 DAL。覆盖真实准备→人工确认→机器 lease/ACK→下载，成功/后续失败/完成顺序、撤权、停用、角色、actor/公司、编辑、报告来源、伪造票面、主档摘要及私有下载；只有本任务内部合成数据。

首两轮 Site 用例8/8均在准备阶段失败，原因是夹具用了导出别名 `description/amount`，旧推送明细需要 `name/subtotal`；中间只加单价未解决。改正合成票据后先验证下载，再跑其余7项。随后补齐失败原因和创建时间/完成时间反序覆盖，最终8项整次通过。失败日志保留，没有修改推送防呆、放宽断言或把失败计成通过。

使用已存在的 `cowork-backend-ack.localhost`，锁定 Frappe16.33.0 / Python3.14.7 和原专有内网 Docker project。无 schema/patch/底座变化，本包不重复新建 Site、migrate 或全套测试。无翻译词条变化，不重复既有 strict i18n；没有跑 DMS、前端、CI、付费 OCR、LINE 或真实 ERP 业务验收。架构与差分检查见验证清单。

## Windows 现场只读取证

证据：`evidence/windows-fifteenth-readonly.json`。本机通过已配置 `lenovo-office-lan` SSH 访问 `DESKTOP-MN2BRKR`，SSH 用户 codexops，两个 Companion 进程运行用户均为 skin3。`C:\Program Files\Pearnly Companion\companion.exe` 的实测 SHA256 为 `0be7e908c3ce38d080f88b57f6eb8bd59f51d0315c3da5ac58dd775b75be448a`；安装注册表显示1.1.77。这不等于 EXE 到构建源码 SHA 的映射已经证明；只读旧 release 文档说明同版本曾有不同构建产物，不能用本地 HEAD 或同版本号顶替。

实际 skin3 profile schema=4，backend host=pearnly.com；两个 profile 均指向 `\\Accserver\d$\ACCOUNT\70EXP\TEST`，method=dbf、paused=false、加密 token 存在。codexops 自己的 profile 列表为空，不能误把它当活动配置。没有输出 token、endpoint/用户标识或 DPAPI 密文，没有复制凭据到隔离实验环境。

SSH 上下文 Test-Path 返回不可访问；旧文档的69EXP/test也不能替代当前70EXP/TEST。尚未验证 skin3 桌面身份的共享目录访问、TEST账套内部标识、独立ERP回读，也未解密测试凭据、修改 profile、启动第二写者或切换 URL。后续实际联调已获用户测试授权，先在正确身份下补齐这些证据，再使用有唯一测试引用的最小写入；不重配现用生产连接。

## 下一步与交接保留

优先补 Windows 已运行 EXE 到构建源码的身份链，以及正确用户下70EXP/TEST的只读访问/账套身份，再做旧 URL 与顶层 JSON 封套到新原生服务的独立接线。测试写入授权有效，正式账套写入、生产切换、部署仍未授权。managed公开运输、MRERP、独立回读、LINE/WeKan、其余导出及旧数据迁入仍未完成。

本包本地提交，任务工作树/分支继续保留；没有合回/推送。专属 lab 容器收尾停止，卷/Site/合成记录及来源/验证日志保留到迁移结束时核归属清理；本批仅清理可再生 Python 缓存。主线/远端SHA及精确保留资源见任务记录顶部，不能以 `git status` 干净声称全仓已经统一。
