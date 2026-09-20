# Cowork 后端迁移（前端接入前）

## 目标与授权边界

用户 2026-09-20 要求按已讨论方案开始迁移，做到需要接前端时再讨论。旧前端只用于提取业务行为；新界面以已调整的新 DMS / 原生 Desk 为参考，当前不改页面、不构建前端。只在本地实现与隔离验证；不上线、不部署、不写真实 LINE / ERP / WeKan，不执行付费 OCR。

状态：进行中。功能清单、实现、本地验证、外部业务结果和用户验收分别登记；代码骨架或状态字段不计作完成。Cowork 范围包括录入、识别历史、推送、主数据、对账、盘点、LINE、Companion 和 WeKan。每项达到可接前端之前均须有来源与行为证据；尚未迁入的模块明确保留。

## 工作树与并行边界

- 新仓基线：`1d7832e6c5c767b310542b9022750992ec9957b0`，集成目标 `main`。
- 本任务分支：`codex/cowork-backend-migration`。
- 本任务工作树：`/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly`。
- 其他窗口 `codex/ssdnodes-migration` 及现有 DMS / auth 工作树保持不动。主目录被控制面绑定挂载，当前不合回、不更新主目录、不推送。
- 现有 Docker context `colima-pearnly`，12.5 GB VM 内存；DMS、auth、control 实例在运行。测试必须使用本任务专有 project / 网络 / Site / DB / Redis / 卷，不调用共享 `stack.sh` 或 `dev-reload.sh`。不重启 Colima，不变更其它容器。
- 公共 Core / 登录 / 语言 / 控制面修改尽量避免，必要变更独立列明并回归，不能覆盖 DMS 窗口的修复。

## 旧线上来源（开工只读回查）

核验日期：2026-09-20（精确 UTC 时间及首尾结构化证据由本任务基准采集器记录）。

| 组件 | 实际回读 |
| --- | --- |
| Cloud Run Web | project `pearnly`，region `asia-southeast1`，`pearnly-web-ec2e5c957e6b-s3`，100% 流量 |
| Cloud Run Worker | 同项目区域，`pearnly-worker-ec2e5c957e6b-s3`，100% 流量 |
| 不可变镜像 | 两组件均 `asia-southeast1-docker.pkg.dev/pearnly/pearnly-app/app@sha256:178af2ed94d406c33929290a47c27c11e96451cb79940c47e96adad0073d7226` |
| 源码映射 | GitHub Manual CD `35321281376`，`conclusion=success`，完整 `head_sha=ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；固定部署账本给出相同 digest / SHA |
| 挂载 | storage / uploads / var / companion 安装包 / runtime secret；未见 App 业务源码目录覆盖挂载 |
| 运行配置引用 | `pearnly-web-env:3` / `pearnly-worker-env:3`；秘密值不写入证据 |
| WeKan VM | `pearnly-work` 当前 `TERMINATED`；不能据历史记录或占位镜像声称当前 WeKan 运行源码已验证 |

旧日常仓库 `master@e4505459` 落后且存在他人脏文件，保持只读。迁移使用 `git archive ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188` 的独立快照：`/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。该路径可由完整 SHA 重建，不是源码权威副本。旧 `origin/master` 比此线上 SHA 仅多部署账本文档，业务来源仍固定为实际线上 SHA。

## 结构与交付顺序

1. 来源核验工具、覆盖映射、Cowork 业务域和权限合同。
2. 真实 Frappe Site 内的公司 / 账套范围、录入 / 历史 / 确认 / 推送事实与失败恢复；使用固定识别输入与假外部出口验证。
3. 识别接入、外部适配、Companion / LINE 协议、对账与盘点，按实际来源依赖拆包；缺少真实版本证据的 WeKan 移植不猜版本。
4. 当后端接口可以承接实际页面时，整理接口、样例和剩余缺口，与用户讨论前端；不自行搬旧 UI。

业务实现进入 Cowork 域包；纯计算不依赖 Frappe / IO。共享身份、购买功能、识别执行和 `Pearnly Push Log` 复用 Core。每 Site 的推送记录是本站权威，不跨库访问 ERP。列表、详情、导出、附件均需相同范围权限；并发去重不能依赖先查再插入。

## 验证与状态

| 层 | 当前状态 |
| --- | --- |
| 开工来源版本 | Web / Worker 已现场核对；WeKan / Companion 的独立运行来源待补 |
| 实现 | 录入/复核/完成/历史、原件、盘点原生后端已有；完整发票纯schema/校验/分组/结果准备/纠错提示/策略已接；OCR供应商和其它模块仍有缺口 |
| 架构 / 离线 / 真实 Site | 第三批架构0违规、199个Cowork离线测试和6项新Site集成通过；第二批55项Site及3组WSGI证据保留，未重复冒充本批重新运行 |
| 前端接入 / 浏览器验收 | 按用户要求暂不实施 |
| CI / 合并 / 推送 | 主线8f1851f已单向合入任务分支；未合回主目录，未推送，未运行CI |
| 部署 / 外部写入 / 真机 / 用户验收 | 未执行，不在本次默认授权中 |

交付前重新读取旧线上身份。若相关源码或配置变化，补齐增量并重验。新仓 SHA、候选源码挂载、安装升级及重复迁移结果、未覆盖项随本记录更新；不把模拟出口当真实外部成功。

## 第一批后端检查点（2026-09-20）

实现与前端承接边界见 [接口与未迁清单](cowork-backend/backend-api.md)。固定来源字段、路由及附件风险见 [录入来源映射](cowork-backend/intake-source-map.md)；对账/盘点与浮点差异见 [对账和盘点](cowork-backend/reconciliation-stocktake.md)；推送当前仅有 [纯预检合同](cowork-backend/push-contract.md)。

新原生数据为 Cowork Workspace、Cowork Client、Cowork OCR History。公司与买方分开授权，完整票面与首次识别快照分开存储。保存使用行锁和版本，完成使用整批范围核对与保存点，重复完成不重复写。手工录入请求幂等由唯一键保护；MariaDB 1020 冲突在单条创建 RPC 的事务边界最多重试两次，内部持久化函数不隐式重试调用方的整个事务。

测试发现并修正的具体问题：

- 原生 PUT 没带客户端 revision 时会从数据库补当前值，不能仅靠 validate 比较版本；现在原生写入拒绝，专用保存 RPC 强制传版本。
- Frappe DocShare 可以回退绕过自定义权限及列表条件；现在详情明确拒绝，列表阻断遗留分享回退，新增 Cowork 分享拒绝。
- User Permission 的其它 DocType 局部授权不能被当成全局公司成员；停用客户从原生列表排除；主档可分页到第201条。
- 两个事务使用同一请求键会遇 MariaDB snapshot conflict；现已实测两进程建立旧快照后同时提交，返回同名且只落一行。
- 批量第一条保存成功、第二条保存失败时，真实数据库回读两条均仍草稿；保存点异常分支覆盖，数据库死锁走整事务回滚。

### 本地验证证据

- 环境：专有 project `pearnly-cowork-backend-lab`，内网 `Internal=true`，无发布端口、无真实凭据、无 worker/scheduler。
- Frappe `16.33.0` / Python `3.14.7`，与锁定矩阵一致；镜像 `sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`，Core/Cowork 只读绑定本任务工作树。
- Site A/B 证明数据库隔离；后加 `cowork-backend-fresh.localhost` 证明新增 DocType 的全新安装；A/B 已有站点升级与重复 migrate 通过。
- 升级前后合成数据/权限/用户设置摘要一致：`b2eac3fd2c980f8f3f176d3b8f5cdd8a986ac8b0412ae34852f5eb3920cab2e9`（16条历史、8家公司、4买方、16条范围授权、16用户、20角色分配）。后续测试另增的合成记录不在此摘要内。
- 21项真实Site用例覆盖成功、越权、Guest、角色、功能、跨Site、即时撤权、原生分享、版本、首存原件、幂等、并发、晚失败回滚和分页。测试只写专有本地卷。
- WSGI 原生 API 实测创建→详情→完成：成功；PUT不带版本=417且旧票号不变；匿名详情=403；GET写方法=403。未启动浏览器，不代表页面或真机验收。
- 90项纯离线测试通过（来源闸门24、history20、push19、reconciliation10、stocktake17）。
- Cowork 语言包沿既有术语表生成。实际 Site 验证 en/zh/th/ja 可加载日期错误与OCR质量文案；通用状态不改成OCR专属含义。日语框架权限文案缺失另由本App补齐。界面显示与泰/日人工用词尚未验收。全仓缺漏检查有其它模块既有缺项，未在本批扩大修复或声称全仓通过。
- 固定源码差分：数量4020、第一轮配对1000、推送纯预检6361组；history来源与直接差分边界见对应记录。
- `python3 scripts/check-architecture.py`、Compose config、差异空白检查通过。本机/镜像无 Ruff，未称 Ruff 已通过；新文件另做 AST 语法检查。未改DMS业务或共享Core，不执行外部DMS回归。

### 线上再次核验

结构化来源凭据保存在 `cowork-backend/evidence/`，采集和离线验证方法见 [基准工具](cowork-backend/baseline-tool.md)。

- 开工凭据内容摘要：`6092bc4e235dc7adb2096db97664881b9101342d9b4f61a729af567281a879a9`。
- 检查点凭据摘要：`6ec3b0fbf990ee5949d57f3d453dbd8dbebb0fbc1681ae9630ef9f774ea9990c`。
- compare 通过：Web/Worker 的实际流量 revision、digest、固定源码、配置引用在本次记录范围内未变。仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。凭据不含 Secret 内容；来源一致不等同全部业务迁移完成。

前端讨论检查点：目前可讨论录入/复核页面如何承接；整产品迁移尚未完成。保持独立分支，不合回正被其它窗口和本地控制面使用的主线，不推送，不部署。

重跑命令限本工作树：

```sh
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml up -d
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T bench env/bin/python /workspace/scripts/cowork-backend-lab-init.py
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T bench env/bin/python /workspace/scripts/cowork-backend-lab-verify.py
docker --context colima-pearnly compose -f infrastructure/compose/cowork-backend-lab.yml exec -T bench env/bin/python /workspace/scripts/cowork-backend-lab-verify.py --http-smoke
python3 scripts/cowork-migration-baseline.py compare docs/work/cowork-backend/evidence/baseline-first.json docs/work/cowork-backend/evidence/baseline-checkpoint.json
```

这些测试在本地专有卷创建合成记录，不能对真实租户执行；HTTP测试是进程内WSGI请求，不访问外网。验证结束停止本任务三个容器以释放内存，保留工作树、证据和本任务数据卷，便于继续；未停止其它窗口的任何容器。

本批实现已本地提交为 `10f7ef449db036d06ca2f477893183aedaf46463`，共享暂存快照钩子通过。收尾发现其它窗口已将主线推进到 `8f1851fcd4e8f4ae05473bdfc761bfd186690f5a`（开户、DMS、侧栏修复）；本任务未回退或改写它。当前验证仍绑定本任务原基线及本批提交，**尚未验证与最新主线合并后的组合**。后续合并须保留双方术语表增量，并按共享配置/身份影响补定向回归。主目录和本任务工作树最后检查均无未提交改动（本记录收尾提交后再核对）。


## 第二批后端检查点（2026-09-21）

用户要求继续。本批先将主线 `8f1851fcd4e8f4ae05473bdfc761bfd186690f5a` 合入独立任务分支，合并提交 `f409bce560df11f78ee5fc7908e71129c4e37a36`；术语表唯一冲突保留双方新增项。主目录及其他任务分支/容器未修改。收尾观察主线又推进至 `d3cf80b1ed2336f7c01f0ae7339543ac929fd7d3`，新增是正式部署工具与记录；本轮不部署，不将此未纳入组合冒称已集成。

### 本批实现

- `pearnly_cowork.files`：原生 Source File + 私有 File，上传原字节/校验 SHA、实际页数、幂等与受控下载。沿固定来源100 MiB/50页门禁；PDF/图片解析失败明确拒绝。原件不去EXIF、不与普通附件共用磁盘路径；普通File的全站设置保持原生行为。
- `history.recognition`：可信内部入口整批保存与封存原件关联；原件初始范围、每张票据范围、物理页证据一并检查。保存 history ID 清单让另一授权用户重放时仍返回原批。等待其它事务绑定后，用 current read 回读这些ID，避免新ID/旧快照混用。不是OCR RPC，没有运行模型、收费或入队。
- 权限覆盖业务原件/按页预览与原生私有URL、File详情、ZIP、公开切换、修改/删除、路径/缩略图/同hash别名。普通File原生读取/权限/删除有回归。DocShare同时在before_validate和validate拒绝，JSON ignore_validate不能关闭保护。
- `stocktake`：四个原生DocType，解码行快照、任务/明细、扫描增改作废、operation回执、累计、关单、JSON报告；关单不写库存账。实测MariaDB1020后，在专用RPC增加最多三次整事务尝试；内部service不隐式重试调用方事务。
- `ocr.inputs/pages`：迁移纯文件门禁、页展开、同页多票与置信度，保留固定源码差分；供应商、完整金额分组及计费接线未搬。四语术语表本批追加106条，仅定向生成Cowork的en/zh/th/ja CSV，每语180条；原有词条保留。

接口及最新缺口见 [后端接口](cowork-backend/backend-api.md)、[文件守卫](cowork-backend/files-guards.md)、[盘点原生后端](cowork-backend/stocktake-native.md)、[OCR接线断点](cowork-backend/ocr-pipeline.md)。没有新增前端或产品导航。旧可搜索PDF/可选加密存储与新上传原字节分开；本批不导入真实旧文件，不宣称旧加密/留底行为已迁完。

### 本地证据

仍使用第一批锁定镜像/Frappe16.33.0/Python3.14.7和专有Compose内网，未共享DMS的Site/卷，未启动worker/scheduler或外部通道。

- `PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p 'test_cowork_*.py'`：106项通过（90项原合同+12项OCR+4项盘点原生合同）。初次误用系统Python且未设置包路径的导入失败不算业务失败；改用本仓App路径和3.14后实际通过。
- 固定源OCR差分：336文件门禁、2000质量批次、200同页多票批次、9非发票场景，通过。未把纯层差分当供应商/完整分组通过。
- `cowork-backend-lab-verify.py`：21项通过；`cowork-files-lab-verify.py`：20项通过；`cowork-stocktake-lab-verify.py`：14项通过。文件覆盖跨公司PDF、同页跨公司拒绝、完整/不完整清单、跨操作者重放、旧快照回放、晚失败整批回滚、撤权、伪造能力/ignore_validate、路径及缩略图别名、同hash隐私副作用、26MiB原件、开启EXIF清除设置仍保留JPEG原字节、普通File权限/删除、两进程并发上传单行。
- 最后连续补测触发Site A原生每小时新增用户限流，失败发生在合成账号准备阶段。未关闭或提高限流；验证脚本现在只允许通过 `PEARNLY_COWORK_TEST_SITE` 选择四个既定专用Site，转到刚安装的files Site后，文件HTTP与盘点14项均通过。没有向真实租户创建测试账号。
- 三组原生WSGI验证：历史创建/修改拒绝/完成；盘点创建/计数/关闭/报告；文件multipart上传/精确下载/PNG预览。越权、匿名、原生owner下载/ZIP/解压/公开切换和GET写方法按预期拒绝；原件及PNG的no-store/nosniff已回读。ZIP在公开入口预检后才交给原生实现，避免打开archive后权限异常导致未关闭警告。无浏览器或真机验收。
- 新增第四个专用 `cowork-backend-files.localhost`，全新安装Core/Cowork与迁移成功；已有三个Site升级/重复migrate成功。前后摘要 `636eb546e39f52facb7a3142e2f97f4e7f547a91c3620592cb90456320683e39` 一致，覆盖30条历史、5个原件及其磁盘SHA、10个盘点任务/20商品/8扫描/8回执、公司/买方/权限/用户/角色。此摘要绑定比较当时数据；随后并发验证又写入标记清楚的合成记录。没有执行真实旧数据库回灌或备份恢复演练。
- 合入主线后的定向集成：DMS离线207断言通过；锁定镜像、只读任务源码挂载、`--network none`下`test_cloud_onboarding.py` + `test_dms_entrypoints.py`共20项通过。只读边界测试，不代表DMS线上业务由本任务验收。
- 架构0违规、i18n生成一致、差异空白检查通过；没有前端改动，不跑前端构建。母语译文、UI、外部业务、CI与部署均未验收。

### 生产来源再次核对

本批开工及交付凭据已保存 `cowork-backend/evidence/baseline-second-start.json` 与 `baseline-second-delivery.json`，离线compare通过：Web/Worker实际流量revision、镜像digest、固定源码与不可变配置引用未变化。

- 开工内容摘要：`3c1c2f256ecf789504401b3549a8b0747d8ff790864dbef5b75e72d5db4be2c8`。
- 交付内容摘要：`dd9495ce2bd0071e0a46a46b12c82b0380317a7f8a701c7d9966d0bcd794b2dd`。
- 仍为源码`ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`及前述镜像。秘密值未写入凭据；OCR运行参数的有效值仍需在接供应商前单独核对，不能从源码默认值推断。

### 后续与并行边界

整体后端迁移继续中，尚未到整产品前端接入验收。下一段依次补固定来源的完整OCR schema/金额闸/分组、Core执行/检查点/缓存/计费、外推日志与写者边界、LINE/Companion、完整对账。盘点还缺XLSX字节导入/导出、照片、旧legacy操作、历史回灌和10000行容量验证；WeKan/Companion独立运行来源仍需核对。不得用现有人工pages或纯OCR函数把这些标记完成。

本批只在任务分支本地提交，不合回正被其它窗口使用的main，不推送、不部署。测试容器在收尾停止，保留本任务卷、工作树和凭据以便继续；不清理其他任务资源。

## 第三批后端检查点（2026-09-21）

用户要求继续，沿任务分支 `392d9d917cb62c1a00d0bb43f72dc7b806082a86` 施工。App/域明确为 `pearnly_cowork.ocr`，新增的schema、金额/日期/来源/置信校验、分组、纠错提示、策略和结果准备都是纯合同；依赖向既有纯history/push合同及Pydantic单向流动，不依赖Frappe/HTTP/时钟/环境。新增Site脚本负责把已验证结果交已有 `history.recognition.persist_batch`。没有增加RPC、队列handler、DocType、产品导航、前端或共享Core默认行为。

### 实现与保持行为

- [完整发票模型及校验](cowork-backend/invoice-contracts.md)：30个票据字段、同页附加发票、OCR位置/词置信度、首次证据、贷项/折扣/WHT/金额来源保留；年份和日期由调用方显式传入。金额运算改Decimal，非有限值与超过float精度的差额显式阻断，差异独立登记。
- [完整分组及结果准备](cowork-backend/invoice-grouping.md)：同号非连续页回并、金额结束页、整件归一、items去重和复写联规则；调用方输入不被合并污染。纯结果核对原件权威物理页集合，缺页/重复物理页/早期失败拒绝；完整有效L2而L3/image-first失败保留为 `review_required` 草稿，不丢掉可人工复核的票据。全非票返回零组及不可作为收费结果的标记。
- `ocr.results.prepare_history_batch` → 已有 `persist_batch`，每组保留所有source pages、唯一权限页集合与独立ai_raw。`has_billable_result`只是有票据结果，不是余额检查或扣费授权。PDF物理页和表格虚拟行不能混用，本接口不冒充表格来源映射已完成。
- [纠错提示纯合同](cowork-backend/invoice-feedback.md)：首存AI与人工修正差分、主页卖方、前4税号/每税号20例/最终12例、去重和提示字节保留；只接已授权样例。原生例库、保存hook、真实历史样例导入尚未接。
- [运行策略](cowork-backend/ocr-runtime-policy.md)：补上只读动态配置取证工具和纯档位解析。实查两个服务invoice均economy+vertex：首读3.1 Flash Lite、升级3.8 Flash。旧env四个模型值虽然写3.5，但被mode覆盖。文本PDF快路和历史纠错注入均开启；不能只照默认image-direct移植。旧Vision回落所需环境缺失已记录，未修生产或试调模型。

新错误码目前是内部纯合同错误，未公开到产品UI；前端/公开API接线时沿现有术语表补齐映射。模型提示块是模型输入合同，不当作产品翻译文案。没有冒称界面、泰/日用词或真实识别质量已验收。

### 验证

继续使用独立 `pearnly-cowork-backend-lab`、锁定镜像/Frappe16.33.0/Python3.14.7；本批确认Pydantic2.12.5。无发布端口、无真实凭据、无scheduler/worker，源码只读挂载本任务工作树；容器内网隔离。不需要新增结构，未重复migrate或跑无关DMS业务。

- 目标运行环境全部 `test_cowork_*.py`：**199项通过**（第二批106 + 本批93）。最终审查修正空fallback与实际L3模型的区分、空timeout回落60秒、证据完整结构/时序检查和独立凭据Secret引用拒绝；补齐断言后，用同一锁定镜像、`--network none`、只读本任务源码的一次性容器运行全套通过，容器自动删除、无Site/数据卷挂载。没有删断言或放宽容差。
- 固定源码哈希差分：980模型；2500 sanity + 2500金额 + 12折扣 + 56日期来源 + 800置信；10固定 + 3000随机分组、11441普通金额、3000税号；5120策略、120模型档位、45区域；3000编辑、6000卖方、3000提示、3000禁用。普通输入严格对照，明确Decimal舍入/非有限/极值差异单列，不称所有输入逐字一致。
- `scripts/cowork-invoice-lab-verify.py`：**6项真实Site集成通过**，写入专有 `cowork-backend-files.localhost` 合成数据。验证同页多票+跨页合并保存、物理页预览、同请求返回原ID、缺页不封存、L3失败可复核草稿、首次ai_raw在人工修改后不变、越权拒绝、全非票不生历史。没有模型调用、真实扣费或浏览器。
- 架构0违规，源码语法与空白检查通过；没有前端或语言包改动，不跑前端构建。安装/升级、55项原生Site、3组WSGI、DMS207断言仍是前批绑定的证据，本批不以旧通过代称新跑。

### 开工与交付生产证据

`evidence/baseline-third-start.json` 与 `baseline-third-delivery.json` compare通过，来源仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。首尾实际流量、镜像digest及固定配置引用未变化。

- 来源开工摘要：`ed778db8459accbf27de222f5c86fe5309b1cfefe8a443a6c1cec9207be5d9cc`。
- 来源交付摘要：`f9db5642104971bd679ba911d4feecd6fa1e3b48038546b280ce952caa7f2423`。
- OCR动态配置开工摘要：`682292fdf84387179fe8f019b216689c0a7c89fb5b86636b5bf6e152c1f626fc`。
- OCR动态配置交付摘要：`e08c07233965dbec5cfe543e9f500d136006cde08fd361b4e2ed78ae6103d104`。

配置取证首尾compare通过。固定Secret只在本机进程内读取，证据只含白名单参数、引用和凭据存在性；数据库只读事务只查一行OCR策略，未读用户/票据/例库数据。瞬时DB读不证明每个进程30秒缓存、用户私钥或实际供应商可调用；未迁真实纠错样例，不能声称模型提示输入和线上完全一致。

### 下一段与收尾

下一段接真实执行链：服务端固定actor/锚定日期/策略及授权样例；PDF文本/200DPI渲染与图像路径；通过Core provider预算/持久检查点调用；同一work逐页串行避免whole-JSON检查点丢更新；结果未知不自动重付费；余额查询失败关闭、保存与稳定reference计费重试分离。缓存、原生纠错例库/保存hook、可搜索PDF、表格回导及原生历史导入需分别补齐。其余推送、对账、盘点缺项、LINE/Companion/WeKan保持第二批清单，不因这批纯层通过被划掉。

主目录收尾观察为 `55d1df4b0a69024c3e7ac446ea98d2697409c13e`，相对上批主目录新增DMS rich-menu修复；未改动、回退或合入此窗口，尚未验证与此最新main的组合。主目录无未提交改动。本批只在独立任务分支本地提交，不合回、不推送、不部署。完成后停止本任务三个容器，保留卷及源码，供下次续接；不影响DMS窗口及其Site/容器。
