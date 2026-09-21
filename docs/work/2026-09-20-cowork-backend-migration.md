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
| 实现 | 录入/复核/完成/历史、原件、盘点已有；PDF/图片/普通表格识别已接原生执行、授权纠错、草稿及计费回执；专用回导、外推与其它模块仍待迁 |
| 架构 / 离线 / 真实 Site | 第五批架构0违规、353个Cowork离线测试、17项新增及63项既有Site回归通过；各批差分、范围及外部替身边界见下文 |
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

## 第四批执行链（进行中，2026-09-21）

用户再次要求继续。起点 `ffdcd477632c8cb435d54ab855c721d4b67779cc`，独立任务树无未提交改动；主目录仍为55d1df4，不改DMS及其运行环境。开工来源身份与动态策略重新只读取证，固定源码仍ec2e5c，首尾与第三批比较通过。

本批责任：Cowork `ocr.engine`负责纯发票编排/提示/响应解析；`ocr.inputs_runtime/providers`负责惰性原件渲染和Core provider适配；`ocr.feedback`负责原生授权纠错例库；`ocr.execution`负责服务端受理、actor/范围重验、不可变策略/日期/提示快照、原生Batch状态、Core work队列接线及稳定reference计费。公开入口只受理本站已有受控Source File，不接受客户端模型、actor、租户或原始识别结果；既有history保存服务承接完整结果，原生业务数据使用新DocType，不手改库。

依赖为Cowork流程→纯engine/输入/供应商适配→Core共享执行/预算，以及流程→history/files/feedback。Core不反向导入Cowork；不改共享默认档，不搬旧线程池，不允许无work上下文的付费调用。模型结果未知不能自动切模型/重做；明确拒绝/限流交Core延后。同work逐页串行检查点，原件惰性取页，余额查询失败关闭；识别结果保存后计费失败只重报账，不重识别。Web/原生CRUD/分享/队列与撤权路径均纳入本地验证。

只在专用内部网络/Site以合成票据和假供应商/假账单出口验证，不配置生产密钥、不发付费OCR、不部署、不接前端。供应商真实结果、旧历史回灌/真实纠错样例、浏览器和用户验收仍需另行证据。阶段实现及最终检查在此处续写，不把设计当完成。

### 第四批实现与审查结果

已接通[原生执行合同](cowork-backend/invoice-execution.md)。受控Source→不可变Batch→Core Work→PDF文本/图片/direct或Vision-L2-L3→完整结果冻结→同事务草稿/原件清单→稳定reference账单回执。公开submit/detail/retry_billing不接受actor/租户/模型/AI结果。只有PDF/image、显式economy/enterprise+Vertex；unsupported格式、auto模式和未配置连接在付费前明确拒绝。

本轮审查修复了实际边界问题，未通过扩大忽略或删除断言解决：

- 供应商已完成的非成功响应必须有终态回放；否则后页限流会让前页拒绝再次发请求。另覆盖Core rejected已提交而产品terminal尚未写的崩溃窗口，原raw缺失如实记为恢复拒绝。本地配置/凭据错误独立硬失败，不能误当L3业务失败降级。
- 长任务不能只清数据库事务：Frappe本进程角色cache仍旧。每次调用和最终保存前重设actor，重新读取角色及范围。
- 正常返回但字段全空的票据原本能漏进结果准备，现拒绝整件，不保存空票收费。这是登记的收费收紧，不称旧源所有边界行为不变。
- `client=None`会被Frappe用当前用户唯一客户权限填默认值。History/Source/Batch显式保存空值，避免没有指定买方的单据被框架变更归属；现场覆盖原件、Batch、历史三层。
- Correction与Batch进入原生query hook必须有All/Guest查询角色，再由hook最终拒绝；直接空permissions会让新Frappe query的旧DocShare路径提前返回。共享Core Work不增加这些权限，使用对象能力/原生生命周期保护和限定Cowork的分享权限升级，保留旧share行，不改DMS份额。
- Core失败通知无hook时返回[]，原代码`.get`使失败/未知任务释放容量前报错。本批仅修这处兼容，8项回归验证真实DMS回调字典和参数仍保留。

框架错误和内部元数据沿TSV生成Cowork中/泰/日/英四语：新增56条，本批70项核对完整覆盖；未接前端，也未获得用户翻译验收。模型提示和旧诊断字面量为来源合同，尚需前端消费时拆分展示翻译，不能直接当用户提示。

### 第四批验证

锁定镜像仍为`sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`，Frappe16.33.0/Python3.14.7，自己的Core/Cowork源码只读挂载。专用Redis6.2 DB6/数据库与内部网络无共享DMS挂载，无外网路由/端口/worker/scheduler/真实凭据。实查pypdf6.15.0已存在，本批Dockerfile显式固化同版本，没有重建或发布运行镜像。

- 最终独立无网络容器完整`test_cowork_*.py`：**285项通过**。首次运行遗漏Core源码PYTHONPATH导致2项导入错误，修正测试命令同时加载本树Core/Cowork后全部通过，没有修改断言绕过。
- 纯engine固定源码hash差分**1919项**；IO同源Vision/缩图/文本路径**540项**，由独立审查记录。不把假response的差分当真实模型质量证明。
- `cowork-execution-lab-verify.py`：**22项真实Site场景通过**，包含两独立进程同时受理、真实Core检查点和Redis预算、原子保存中断/重试、未知结果不重付费、先存后账和丢回执、撤公司/角色、权限/WSGI原生接口、50物理页完整清单与一次按50页记账。所有provider/token/账单出口是假实现，未发真实调用；50页为小尺寸合成PDF，不是大扫描件或多租户峰值承诺。
- `cowork-feedback-lab-verify.py`：**16项通过**；修正原生client默认填充、legacy share列表旁路，以及不符合真实已规范化AI页的fixture后复跑，不放宽断言。
- 受影响既有History原生回归**21项通过**；File/整批绑定回归**20项通过**。盘点未受影响，不重复其旧全套。
- 新建`cowork-backend-execution.localhost`完成空Site安装；原四Site完成结构升级，execution Site后续migrate与重复升级成功。涵盖原件字节、历史、Batch、Correction、Core Work、用户/权限/偏好的前后摘要均为`9bfd48b665f1a6a3de30aca20dd392720e2d5b35974816d9b63f6429f571b94f`（148原件、46历史、101Batch/Work、4Correction）。未做真实旧数据导入或生产恢复。
- 架构检查0违规、空白/语法通过，DMS离线207断言通过。术语表检查通过；**全仓i18n严格检查仍失败**：既有与纯来源模型/诊断中文共147项检测，另其他App每语缺22个英文msgid。没有扩大忽略或改DMS语言包来造通过；本批英文服务器消息/元数据四语覆盖与全仓静态通过是不同结论。无前端改动，不跑前端构建。

### 第四批首尾线上证据

`evidence/baseline-fourth-start.json`/`baseline-fourth-delivery.json`和对应`ocr-runtime-fourth-*.json`均首尾compare通过；Cloud Run实际100%流量revision仍为`pearnly-web-ec2e5c957e6b-s3`及worker同源，源码、镜像和OCR策略未漂移。原代码/运行配置身份不等于新平台真实识别结果已一致。

- 来源开工摘要`6675134220bf2349153b79aa1fa30bf87fcd0c8cab2e200fb9e41a8f4f0e3a4b`；交付`2cf98e0a529d219f5bbf71f4bffcc89137b9ee6219f2dea6d4124d2b1930a53a`。
- 动态配置开工摘要`994be70bf797ff2b4d09f7dc2a4d783a03a1e4679cd1ebd892787de41a09bd15`；交付`d07e7c9f99342c130f0566649517cc28e4bed8b011a48b6004a4c05516d55fa3`。
- Secret只在本机进程内读；数据库只读核对一行策略。未读取票据、客户或真实例库，未修旧线上Vision凭据配置。

### 后续边界

尚未到整个Cowork接前端的讨论门槛。后续优先补表格/虚拟页、可搜索PDF、历史导入和非staged缓存，再接外推连接/写者锁/完整日志回读、对账余项和盘点缺项；LINE/Companion/WeKan独立来源与接口仍需继续核验。真实OCR、账单、部署、浏览器/用户验收均未进行。

主目录本次末读仍为`55d1df4b0a69024c3e7ac446ea98d2697409c13e`且干净，未改其它任务分支、Site或容器。本批留在`codex/cowork-backend-migration`独立树，本地提交，不合回main、不推送、不部署；未验证和最新main组合。收尾停止本任务三个容器，保留标记的合成数据与工作树供续接。

## 第五批：结构化原件识别与原件关联（2026-09-21，本地完成）

承接第四批本地提交 `ecdfd99ded2ea73c365d5b15922fbd4a3614f7a5`。开工工作树干净；主目录为 `55d1df4`，本批未合入主目录新变化、未接管共享DMS运行环境。

开工重新只读采集 Web/Worker 实际来源与 OCR 运行配置，两类比较均 PASS：实际生产仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。开工证据 `baseline-fifth-start.json` SHA256 `7ae9d5fb97fbf1ef563a74d5dba7e14ebe69befde6d167bad611768f30011362`；`ocr-runtime-fifth-start.json` SHA256 `4ae8163362bf8aa46d524ba7c51cab77e1188c355bd107a367070870b4a4db3b`。表格路径未引入额外动态配置键，继承已冻结模型/L2超时/日期/fewshot/金额置信配置。

- 所有实现仍在 Cowork 域：`ocr/table_inputs` 负责本地字节解析，`ocr/table_engine` 负责无IO的L2编排，既有 `ocr/execution` 负责原生队列/权限/结果/计费，`files`/`history` 负责不可变逻辑区段清单。
- 保留旧普通表格合同：Excel每sheet一逻辑区段，CSV/TSV/DOCX/DOC/TXT一逻辑区段，禁止调用图像直读/Vision/L3。字符计费保持旧原始输入估算（包括纯表DOCX为0），不拿sheet数或发票数替代费用单位。
- 原件上传保持原字节与原请求指纹；受理识别才冻结逻辑清单。未冻结的document不能用旧占位page_count=1证明全文已分配。部分内容被旧结构化路径遗漏时保留证据并限制原件访问；所有文档都不伪装成PDF页面。
- 有意变化：拒绝资源超限与模型实际输入超过30,000字符的原件，不继承旧静默截断。保留旧表头覆盖/首表读取等提示词行为，同时记录不完整覆盖。专用roundtrip Excel指纹命中明确拒绝，本批不改走LLM；独立往返导入协议留待后续迁移。
- 本地lab只在原镜像上新增 `python-docx==1.2.0`；openpyxl3.1.5、et-xmlfile2.0.0已存在，lxml保留原生6.1.2（来源6.1.1）。候选镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，源基底 `sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3`；增量构建仅 `RUN env/bin/pip install --no-cache-dir --no-deps python-docx==1.2.0`。生产构建定义同步锁定四项依赖，未构建/发布正式业务镜像。


### 第五批验证结果

- 生产交付复核：`baseline-fifth-delivery.json` SHA256 `646b3e4d0174753c0ca67d701cf60e1d9e53561f31c05b4bc7a3f6698cbf57f8`、`ocr-runtime-fifth-delivery.json` SHA256 `afc275ebd5aa25aa589f7a9290d7860e0eb22a9fde700c6412474b36a83fc327`；均与本批开工比较 PASS，实际业务来源及相关配置未变化。结构化证据在 `cowork-backend/evidence/`。
- 最终候选镜像（上述d5c874…）`--network none` 跑全部 `test_cowork_*.py`：**353项通过**。其中新增表格engine20、输入20、原件manifest28；既有285项保持通过。固定源码差分另外执行：输入345组Page全部字段及字符单位相等、5种失败一致；引擎1500指纹和288提示/决定相等，另1条验证30K提前拒绝这一有意差异。详见三个模块记录。
- 专用 `cowork-backend-table.localhost` 全新安装；现有 `cowork-backend-execution.localhost` 升级；两者重复 migrate 通过。既有 Source150、Batch101、History52、Correction4、CoreWork101、UserPermission24、User29严格按原字段比对不变。原生fixtures重建了20个Page的Has Role子行随机ID，128条角色语义集合保持一致，用户角色未变。保存比对基线 SHA256 `5bb312e7d8ec7492ebe7b4de46f973400dfa2844b0d01b768d6b531e45b108f6`，不把子行ID变化说成整个数据库字节不变。
- 真实Site **17项新增**：多工作表仅L2/一笔字符计费、CP874与计费字符口径分离、纯表DOCX零单位、旧Batch新字段空值仍可运行、二连接RR旧快照幂等受理、Word遗漏/重复列头/部分sheet/未冻结文档的整原件拒绝、配额断点不重买前sheet、整件失败不落库不扣费、30K/损坏/回导模板提前拒绝、清单不可变、账单回执单独重试、即时撤权。真实File/SQL/Redis/Work路径运行；仅模型、token和计费传输替换。
- 原有Site回归：识别执行22、原件20、历史21，合计 **63项通过**。本批没有改DMS业务或共享Core；因候选镜像依赖及运行装配变化，仍跑DMS四组离线 **207项通过**。所有云端写入、真实OCR、真实账单和业务推送均未执行。
- 最后原生断言发现并修正：Frappe `_save` 会覆盖 `flags.ignore_version`，需要显式 `save(ignore_version=True)` 才能防止原件清单复制到Version。已改实现和原生/fake回归；同时核对Correction的track_changes=0，未擅改无此问题的其它域。先前失败只产生专用lab合成测试记录，保留为验证痕迹，没有真实资料。
- 提出过RR快照重复受理候选，已以两个真实进程的READY/GO屏障验证：第二连接读快照在第一请求提交前建立，并确认RR及旧查询仍不见Batch，再经现有有界API重试返回相同Batch、库内仅一条。未复现未捕获的重复插入，不无证据扩改该流程。
- 四语新增30词条，Cowork每语266项；新增字段、错误码与服务文案缺漏0、重复0。生成器check通过，真实Site en/zh/th/ja共12次原生翻译查找通过。全仓既有23个msgid×3语缺项仍在，不声称严格全仓i18n通过；页面布局及母语业务措辞未验收。
- 架构检查0违规、Compose解析、Python语法与diff检查通过。专用环境仍无外网路由、端口、凭据或worker/scheduler；不接管其它任务容器。

### 第五批交付边界与下一步

普通表格识别与逻辑原件关联完成到本地后端验证。仍未接前端，不上线/不部署/不推送；未合回主目录，保留独立工作树与本地提交检查点，防止干扰并行DMS/平台工作。

下一段是专用Excel往返导入、导出以及外推的权威日志/幂等恢复链；随后按依赖推进LINE/Companion、对账剩余路径及其它已登记缺口。真实BIFF xls旧路由不可用、DOC仅有限解码、旧长输入静默截断等已逐条登记，不能宣传全格式或整个Cowork已迁完。WeKan当前运行来源仍不足，未启动或猜版本移植。前端正式接入仍须在后端承接完整后再与用户讨论。

第五批收尾已停止本任务 bench/db/redis 三个容器；Sites/DB/日志卷和合成验证记录保留。bench 的 `sleep infinity` 在停止宽限期后退出137，停前全部测试已经结束、没有运行中的worker；db/redis退出0。再次确认候选镜像d5c874…、源码挂载仅本任务树、无端口发布，未改其它项目资源。

## 第六批：复核工作簿导出、修改与回导（2026-09-21）

### 目标与基准

用户要求继续，并强调DMS迁移曾出现功能不一致；本批按真实旧链证明一致。选择专用Excel往返，前端仍不接入；不上线、不部署、不推送。继承第五批本地基线 `669888001871f5e8cc4bbc783b0ff6c2499d76c5`，仍在独立 `codex/cowork-backend-migration` 工作树，不更改并行DMS目录。

- 开工来源 `baseline-sixth-start.json` SHA256 `48c4d501204ed1ce5afcce25df44094021825249e1d7681177c47b6219e910d9`；开工配置 `ocr-runtime-sixth-start.json` SHA256 `de656e8fdfb9e9ed58873e69e22dee1aced3abeccc9b4747b83358e266cbff23`，两类均与第五交付一致。
- 交付来源 `baseline-sixth-delivery.json` SHA256 `df945185b16472aa369da3f4f6f1aad4ca760ee4907b04f8e771762dc349dbfd`；交付配置 `ocr-runtime-sixth-delivery.json` SHA256 `f1cb0d01c79878061ac6de58504192ce37c9af817209e0c532f62009cdb9ac37`。start/delivery比较均PASS。
- 权威源码仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，web/worker对应revision各100%，既有镜像digest和相关配置未变。结构化证据在 `cowork-backend/evidence/`，不是凭本地HEAD猜线上。WeKan实际运行来源仍不足，未移植或启动。

### 实现与明确差异

App为pearnly_cowork；新增roundtrip业务域，复用OCR纯schema、表格有界解码、Source/History/Batch、Core Work与计费。无新DocType或共享Core业务改动；唯一规格/金额来源和依赖见架构文档及[整链合同](cowork-backend/roundtrip-native.md)。

- 固定泰文四表导出、列位/公式/隐藏PRN键、Decimal金额和逐票回导；每张解析票一个逻辑单元，保留sheet/源行/键/原字段。额外内容不因新票全绑定就向其他读者开放整本原件。
- 受理、执行、冻结结果恢复保存时复核PRN当前Site/公司/客户权限；无旧UUID映射明确拒绝。回导新建历史，旧历史及ERP数据不动；工作簿ERP号不是防重事实。
- 零模型调用但保留旧字符收费，余额准入和原子保存后单笔稳定引用结算。幂等重试不重复买识别或建票；确定性回导不要求无用的模型凭据。
- 修复旧二次按票号误并跨原记录/跨方向单据、整批方向覆盖待判、无票号待判被新完整性闸丢弃。正常解析字段保持来源，以上保存语义有意改变均明确登记。
- 修复原生多页历史导出重复追加首票已合并商品的问题；可信绑定只取首主票字段，未绑定多页不猜。items.subtotal明确适配amount并校验守恒；含税总额当税基加税等不能无损往返的输入明确拦截。
- 命中空表、异常带空格sheet、50连续空行后有数据、非有限数不退付费模型；用户文字不变公式。旧float半分与Decimal差异明确验证，不冒称逐格全等。
- POST导出1至100条，必须全部可读、同公司/客户；失败不跳行。无前端入口；ERP事实查询未接，原生导出状态/ERP号/新建主档清单空白，不冒充已推送。

### 验证结果

- 沿第五批候选镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，没有依赖升级或生产镜像构建。核对本任务lab的只读挂载、独立卷、internal网络、无端口/凭据/worker/scheduler；新增守卫内 `cowork-backend-roundtrip.localhost` 安装和migrate通过，既有Site未重置。
- 最终 `--network none` 全部 `test_cowork_*.py`：**385项通过**（353既有 + parser16 + export16），无skip。
- 固定源解析差分：5文件SHA绑定，**162份正常工作簿、1048个Pipeline页**字段相等（时间置零、显式同年、只剥新增审计）；另1521金额、21数字、20规格比较，2种同样Decimal失败，4种有意保护差异。
- 固定源导出差分：6文件SHA绑定，**60份工作簿、9999单元格、196个回读单据**一致；列位/公式/类型/格式/隐藏/宽度/冻结均核对。另VAT半分样本旧float0.11与Decimal0.10明确不同，打印票面VAT和回读总额保持0.10/1.60。纯算法差分不替代真实Excel软件或ERP验收。
- 新原生往返 **15项通过**（14项整套+后增WSGI专项1），覆盖导出→改票号→上传→新History、金额与旧记录不变、同号两销项/一进项/一待判四票、空号待判、未映射/跨公司/跨客户键拒绝、冻结结果后模拟引用不可读零新票零扣费、回执重试、重复执行、原件私有、两页商品不重复、手工多页拒绝、POST下载鉴权/no-store和匿名拒绝。主体运行真实原生链，外部计费替换；保存中断与引用不可读以定向故障注入验证，不冒称实际撤权。确定性路径断言不构造模型客户端。
- 受影响原生回归：普通表格17、PDF/image执行22、原件20，**59项通过**；含新增合计74项。第五批“回导未迁入”拒绝用例改为“命中空模板明确拒绝”，未删失败断言。
- 新多页case初次是测试helper参数写错（pdf()本来固定两页），修正后该case通过。负面HTTP测试预期PermissionError日志不等于测试失败。
- DMS四组零IO **207项通过**；未改DMS或Core业务。架构0违规、diff检查通过；四语和最后Git检查见收尾补记。

### 尚未完成的边界

本批只证明工作簿这段本地后端承接及明确差异，不证明整个Cowork与旧线上完整等价。ERP配置/真实push日志/写者锁/回读恢复及Companion重推、旧历史UUID与文件迁入、其它导出格式和外层归档/邮件/自动分配、LINE、对账盘点剩余路径、WeKan、真实账单/模型质量/真机与前端验收仍需推进。

下一工作包接权威ERP推送日志和防重/恢复，使回导原键连接旧ERP单据状态；本次生成xlsx或本地History成功不等于重推闭环完成。未来推送须从不可变Source清单/Batch结果取旧键并重查授权，不使用可编辑pages.fields.history_id代替来源事实。前端仍在后端承接阶段完成后讨论。

第六批仍未合回主线、未推送、未部署；主目录最后核对为干净 `55d1df4b0a69024c3e7ac446ea98d2697409c13e`，未把其后变更混入本任务。独立工作树通过不代表主线集成通过。

第六批收尾补记：四语新增22词条，Cowork每语288项；22个新增键无缺漏/重复/跨App冲突，仅生成Cowork四份CSV。生成器check通过；专用Site原生en/zh/th/ja全部88次查询与术语表相等。真实母语业务措辞及页面尚未验收。最终独立审查指出的多页商品重复已修复并经真实保存回读覆盖；故障注入证据措辞亦按实情收紧。业务检查通过后只补语言、文档和Git收尾，不重复已通过的业务全套。

收尾已停止本任务bench/db/redis三个容器，Sites/DB/日志卷及合成样本保留。bench是sleep进程在宽限期后退出137，测试已全部结束；db/redis退出0。未清理其它工作树、数据库或容器。本批形成独立本地提交检查点，未合回主目录、未推送、未部署。

## 第七批：ERP 目标、Express 准备与恢复合同（2026-09-21）

### 范围与开工证据

承接本地 `407ad132b2e69c871bcb272e2a7a361b6363d295`。仍在独立 `codex/cowork-backend-migration`；开工主目录为干净 `3bacdbd996d90880f61a108fd584a50c0af36a9c`，本批没有合入或修改它。用户继续授权本地后端迁移，强调行为一致；前端、不上线、不部署、不向真实 ERP 写入的边界不变。

- `baseline-seventh-start.json` SHA256 `d3f6ed40079ebf9049096e007a51ac3ee5cf581614004c558c46717e718449a5`：Web/Worker 实际100%流量仍为 `ec2e5c957e6b` 对应 revision，完整源码 SHA、digest 与第六交付比较 PASS。
- 先补推送专用只读采集，再开始业务纯代码。`push-runtime-seventh-start.json` SHA256 `111119d806e9daed01724baeec825453432078020717e21d484a4a7fe723573a`：两服务 `ERP_PUSH_ENABLED=true`，动态 shared/target_projection 开关 enabled/all；60 个端点全部 generation0，managed 数0。Express启用7，6个有 token/协议1；历史自报版本不是客户机二进制证明。详见[推送运行基准](cowork-backend/push-runtime-baseline.md)。
- 没有读取业务票据、凭据明文输出或客户机文件，没有触发建表/修表。目录、映射和其它未取的动态值不冒充完整业务配置快照；source固定只证明服务端算法来源。

### 本批职责与验收边界

本批先迁完整的纯目标选择/证据判断、Express进销项预检与载荷闭包、队列状态/权威回执判据。没有可靠目标收集器前不创建可误认为待发送成功的原生记录；不新增 Push RPC、worker、DocType 或导航。下一段才接原生 Connection/Snapshot/Refresh、Push Record、当前权限与写者锁。

- `targets`：固定来源目录规范化、账套选择、refresh与projection证据、切换目标清理旧科目缓存；服务端注入权限/目录后冻结目标，不能把用户config当权威证明。
- `push.express`：预检、进销项payload、科目/金额/税/付款/库存策略。显式注入日期、已授权映射/供应商/银行和原凭证号；`ready`仅表示这些输入通过准备，永不等同 queued/success。旧 `stock_lane_enabled` 实际恒False，不凭函数名扩展成开关。
- `push.lifecycle/retry/receipts`：旧legacy与managed分开；等待锁的attempt不同；确认放行后失联不重领；HTTP reservation结果未知转manual；显示最新状态与历史成功防重分离。
- 旧legacy ACK没有attempt fencing、managed确认任务过期可停在不可重领pending、旧PRN查失败返回空以及跨账套查询都是已发现的来源缺口。保留纯行为证据并登记；原生外推接入前须明确修复，不能把本批合同当作安全执行器。
- PRN纯查询明确改为同原History/租户JOIN/端点/账套；失败查询与成功空结果区分。原生调用尚未连接，未来必须从不可变Source/Batch取原键并重新授权，不读可编辑fields作为权限事实。

### 第七批验证与交付范围

- 候选仍为第六批 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9` / Python3.14.7，没有增依赖或重建镜像。仅用 `docker run --rm --network none`（512MiB/1CPU）只读挂本工作树执行纯测试；没有启动bench/db/redis，没有Site结构或数据改动。
- 最终所有 `test_cowork_*.py` **631项通过、无skip**：385既有、Express170、targets28、lifecycle/receipt29、运行采集17、纯依赖边界2。独立复核后的修改已在同一候选环境重新验证，非沿用修改前结果。
- 候选运行时固定源码差分：Express **1,013组/28固定源hash**、targets **7,156组/7源hash**、push状态与回执 **1,262组/7源hash**（229分类、480 ACK参数效果、340外单号、5冻结、208发起层分类），全部通过。这是合成输入和替身读写口的算法对照，不是9,431次真实建单或原生队列验证。目标组合另外6项源接受而新冻结拒绝的案例单列，不计入相等数。
- 独立审查复现并修复目标root与最终程序目录不一致、证明账套B但实际config残留A的问题；默认账套使用服务器明确根目录，保留无需刷新目录的旧行为；MRERP年/库分别trim，不以过严保护缩减原功能。缺真正根目录/非默认仅父目录推断/目标相矛盾的输入明确拒绝并登记。
- 修复零次attempt只在legacy失败分支规整、SQL普通空格/lower与Pythonstrip/casefold误等价；补终态晚ACK不得回退、Express技术失败与数据错误的调度区别。PRN查失败明确拒绝、同目标回执限制为有意收紧；其它已登记旧行为没有顺手改动。具体案例与范围见[状态与回执](cowork-backend/push-lifecycle.md)、[Express载荷](cowork-backend/express-payload.md)、[目标合同](cowork-backend/push-targets.md)。
- 初次source v1配置摘要没有覆盖所有端点config键，独立审查指出后增加v2完整JSON的数据库内集合摘要，不返回任何值。补充基准 `push-runtime-seventh-expanded-start.json` SHA256 `638c13bf1b7572bb1a649f2ef481deef4ed0301871be025bdb27fe4926fa84f1`；只证明补充采集时刻起的范围，未回写或替换最初v1。
- 交付来源 `baseline-seventh-delivery.json` SHA256 `2b66ac9fe67b7ba68c1a6780e78ee4b62bf3d034a54c2842f60191aad578d3df`；与start compare PASS，生产100%流量revision/SHA/digest未变。v2交付配置 `push-runtime-seventh-delivery.json` SHA256 `93d9019400e052a32f7dcfcd21486cde6a22188260f22b194d85ee272578f199`；与expanded-start compare PASS（settings不变、observations不变）。5份原始脱敏证据在 `cowork-backend/evidence/`。
- 源定义通过stdin传给无网容器并在容器内核验hash。曾直接挂宿主`/tmp`固定源失败（Colima未共享，文件不存在），没有执行到差分；换stdin后成功。没有绕开哈希校验、原生钩子或删失败断言。
- 本批没有改DMS/Core、共享配置或运行依赖，按影响面未重复DMS/Site/前端检查；现有通过记录只代表原批版本。新增纯异常码/诊断没有接产品入口，不宣称原生四语页面已验收，也未扩大既有i18n忽略。

### 下一段仍需接通

原生 Connection/Snapshot/Refresh和唯一Push Record、当前角色/公司/客户/连接授权、不可变PRN原键查询、端点/账套写者互斥、持久化预留与attempt fencing、真实队列和ACK、失败回读/原目标恢复，才构成可用的后端外推链。MRERP HTTP适配/真实回读、Companion当前执行文件到源码的证据、WeKan、LINE、旧记录导入等前批余项仍在；没有用本批pure ready关闭它们。

前端仍暂不接。未合回主线、未推送、未部署；主目录末次只读核对仍为干净 `3bacdbd996d90880f61a108fd584a50c0af36a9c`。本任务分支的纯规则通过不代表与最新主线已集成或用户已验收。

第七批收尾：架构检查0违规，纯代码无框架/IO/墙钟依赖检查通过；没有新依赖、结构、运行入口或前端，不重复无关业务套件。专用bench/db/redis继续保持停止，既有合成Site/卷原样保留；临时无网测试容器自动移除。本批保存独立本地提交检查点，后续从原生连接和权威账本装配继续。

## 第八批：原生连接、推送准备与持久账本（2026-09-21）

承接本地提交 `0e5cd94e84f444c2246b57b4838263163c9cdfc3`，独立工作树和不接前端、不推送、不部署、不写外部ERP的边界不变。开工工作树干净；主目录仍为 `3bacdbd`，不将并行DMS改动混入本批。

开工来源 `baseline-eighth-start.json` SHA256 `95f1e96fd6560046d7d88db9b90a80b739ec38aca331a619e78d3e9e0935f1f1`，运行配置 `push-runtime-eighth-start.json` SHA256 `1389e17789dbe9416021d16383b880034f9ed847e5a06101549c79d411342983`；两者与第七交付比较PASS。实际源码仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；客户端实际二进制及未采目录关联表不在这些证据范围内。

本批职责：`connections` 持有当前Site的连接授权/配置与目录刷新快照；`push.application` 在当前用户下锁定历史、连接和服务端证据，调用既有纯准备；`push.native` 持有唯一推送记录、不可变载荷和尝试/写者状态。依赖只从装配层指向连接、原生账本和纯规则；连接不反向导入推送。新增DocType按原生迁移维护，通用REST和DocShare不得绕过专用服务。记录准备完成不表示已排队到真实客户端或入账成功，不挂接scheduler/外部消费者。

计划在专有无外网lab完成新Site安装、已有Site升级和重复迁移、并发请求、防重、陈旧ACK、未知恢复、撤权和原件PRN来源验证。先做本地模拟运输，真实Companion/MRERP及业务验收不由此代替。


### 实现、保持的契约与明确变化

- 新增 Connection/Snapshot/Refresh、Push Record/Attempt/Writer Lock 六个原生DocType。连接管理、授权、目录选择和安全DTO已接专用接口；credentials进入原生Password，不进入公开DTO。没有菜单/页面或公开运输/ACK入口。
- `prepare` 在连接与票据当前锁下复核身份、公司/买方、显式连接授权及版本；公司税号读当前Workspace，准备资料必须完整且匹配账套，不能把缺映射当已查询为空。Express pure ready装配为持久 prepared；仍无真实collector和外推消费者，MRERP明确未接通。
- 同请求按原参数/冻结结果重放，当前授权与不可变PRN原票权限仍重验。旧键只取封存Source清单/唯一saved Batch与冻结结果的共同事实，可编辑history_id不能改防重来源。成功原凭证查询沿源兼容express_docnum/meta.docnum，读取失败或缺凭证号不伪装为空。
- 同scope/revision/generation的目录请求合并同一requested ID，保持旧线上行为；不同request key的durable aliases保留重放，已完成后新key才新请求。版本改变不能重绑旧key。alias保留策略尚未实现，不冒充真实采集队列。
- 本地唯一账本、跨连接同公司/root/account互斥、每次领取独立随机令牌摘要和递增fence。晚回执不能污染新尝试，成功不被晚失败覆盖。已领取超时保留锁并标未知，绝不自动重发。ACK格式沿实际legacy/managed调用闭包，只有成功保留line_modes；legacy才派生fallback_count/created_masters。
- 原生全量耐久reserve、全部attempt fencing和未知保留锁是明确保护改进，旧legacy并非本来如此；没有假称逐行搬运。当前actor/History/Connection/branch/generation与Record冻结值必须一致，当前目录撤销也阻止派发。只有prepared、fence0、无active_attempt、查无任何Attempt、非未知，且原版本过期或原actor当前权限明确失效，才可由新已授权请求原子替换；原payload和操作者审计保留。当前旧actor仍有效时不能抢占，数据库异常不当撤权。
- 所有新DocType通用REST/Desk CRUD及DocShare拒绝，真实专用接口仍走当前角色与范围。内部Python AdapterContext不是Companion身份认证；不能借合成ACK声称真实外推成功。

### 本地验证与最终来源

候选镜像仍 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，Python3.14.7/Frappe16.33.0；没有依赖升级或生产镜像构建。Docker context `colima-pearnly`、project `pearnly-cowork-backend-lab`，只读挂本树代码、独立Sites/DB卷、internal网络、无端口/外部凭据/worker/scheduler。bench为sleep进程。

- 新 `cowork-backend-push.localhost` 安装与迁移通过；最终alias字段追加后再次migrate。既有 `cowork-backend-roundtrip.localhost` 最终结构升级和重复migrate通过，14张原有业务/权限/用户表、340条记录的规范化摘要完全一致：`9990203abcd9624875cadff72842c23ffadf58baf67d22951b765cf663e19e20`。用户只纳入name/enabled/type/language，不读取密码。六个新增表不涉及旧业务数据转换，未执行破坏性清空或另一次全库恢复演练。
- 最终离线无网候选：所有 `test_cowork_*.py` **652项通过、无skip**（631既有+连接9+原生合同12），日志 `/tmp/cowork-eighth-unit.log`。纯规则边界仍覆盖所有第七批纯文件，并新增相对依赖检查；native/application有明确IO职责，不通过扩大纯模块白名单掩盖依赖。
- 新原生 **63项通过**：连接21（2.134s）、推送账本24（7.997s）、应用与不可变PRN装配18（3.641s）。分别见 `/tmp/cowork-eighth-connections-final.log`、`/tmp/cowork-eighth-native-complete.log`、`/tmp/cowork-eighth-application-final.log`。含两真实数据库进程同/不同key竞争和双领取、旧RR快照后的税号更新、实际撤角色/范围、原生Password、两连接同账套互斥、错误token/fence、未知锁保留、晚ACK、防重/取消/恢复及事务中断回滚。内部运输与计费使用替身，无外部业务副作用。
- 18项应用用例包含真实WSGI认证POST prepare→detail→cancel，以及GET写操作、Guest、原生GET/PUT旁路拒绝；负面PermissionError日志为预期。PRN含实际导出/上传/识别保存来源链、篡改可编辑键无效、原引用撤权拒绝。旧往返受影响回归15项通过（2.916s），合计本批 **78项原生测试**，没有把重复运行次数叠加。
- 受影响receipt纯对照在候选无网容器核验7固定源hash后通过：229分类、480 ACK状态效果、340显示单号、5冻结、208发起层结果，共 **1,262组**。当前24项账本额外覆盖legacy/managed×6种stage的12组响应字段合同。它们不是真实建单数，不证明实际Companion或外部回读。
- 初轮native22项失败11/错误1：测试进程提交的holder与后续同公司/root/account fixture相撞，正确写锁拒绝领取；非默认账套fixture缺独立映射；并发RR冲突需要调用方整段本地事务重试。修复fixture隔离与完整准备包，合成worker仅对QueryDeadlockError最多3次rollback后重新授权/领取。没有吞错、删断言、放开锁或重试外部IO。此前应用current税号测试暴露真实MariaDB1020，专用无外部IO RPC亦有界重试；内部service不擅自重试整个较大事务。
- 独立审查发现的meta凭证号漏读、精确head回落、冻结context可被新context复活、目录撤销后仍领取、撤权后预留永久阻塞均修复。三项最后组合问题已在实际函数定向审查及原生测试关闭；[审查记录](cowork-backend/eighth-source-review.md)保留证据边界。
- 语言包新增116词条，仅生成Cowork四份CSV；每语404项，en/zh/th/ja共464次新增词条原生查询与术语表相等。新增错误码及六个DocType label均从产品已安装Core/Cowork包可取，补复制原先只存在Control的Started At。母语措辞和页面未验收。
- `i18n-build --check`、新增词条检查、架构0违规、Python语法和diff检查通过。全仓 `check-i18n --strict` **未通过**：147处/43文件/135条，抽取提交基线HEAD用同脚本复核后结果完全一致（只规范化源码行号），本批零新增、未扩大keep-cjk；这仍是待处理旧语言债务，不写成全仓语言通过。
- 交付来源canonical evidence checksum `61ab9d88c93942de9a42bbedd1e5bd7d2a0f62a1e1f726c6928bdfe9b1e1e640`；交付运行checksum `54e821dd71557b0360c96d546ab9c513512526c9093ae344a60a2800de6eb699`。本批start/delivery比较PASS，Web/Worker实际流量对应源码、digest与采集配置未变，settings_unchanged=true / observations_changed=false。四份脱敏原文件已存 `cowork-backend/evidence/`；这些是规范化evidence内容摘要，不是含校验字段的JSON文件字节hash。

### 仍未完成与下一段

本批完成的是本地持久准备及账本，不是整条ERP外推或Cowork迁移完成。下一段需把真实Companion身份/绑定/目录采集与运输、ACK及未知回读恢复接到这些入口，补旧成功日志和原History UUID映射；实际Windows运行文件来源未核验前不移植未证实客户端。MRERP HTTP、History状态镜像/stock-master计数/通知副作用、导出ERP显示列尚未接权威账本。跨Site/跨公司或别名路径同物理写者也尚未证明。

更早余项仍包括LINE、对账/盘点剩余路径、其它汇总/导出/归档、旧文件及历史迁入、WeKan来源与迁移，以及真实模型/账单、业务与用户验收。前端仍未开始，待后端范围承接到位后再讨论；不搬旧UI。未合回集成主线、未推送、未部署；主目录末次只读核对仍为干净 `3bacdbd996d90880f61a108fd584a50c0af36a9c`，没有改并行DMS工作树或运行组。


第八批收尾：本任务bench/db/redis三个容器已停止（sleep bench退出137，DB/Redis退出0）；全部测试已结束，独立Sites/DB/日志卷及合成数据保留，未清理其它工作树或容器。提交使用共享钩子检查实际暂存快照，本地保存本批检查点；未合回主线、未推送或部署。


## 第九批：Companion 凭据与盘点导出（2026-09-21）

用户继续本地迁移；承接 `e23b17afa0e881d2c89de7fe930c17d26358c3f5`，独立工作树开工干净。主目录已经前进到 `2efa0ac027ca068a6e998a87d2029f30019f2365`，未将其部署工具等增量混入本树；没有修改DMS/主线或启动其它运行组。框架矩阵和本任务镜像不升级。

第九来源只读回查 source仍为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，canonical checksum `1bf8b32e0d4e84128a3b8641450e40923b5bfc1470c1d76fda5a5d11607506c7`，与第八source比较通过。但运行配置摘要 compare **拒绝**：两个服务的60端点集合摘要都由 `461d6540e21d14f3a41443ad720d5605` 变为 `5ee9e6604b3c6dee19ab3227d8484fad`；其它identity、flags和observations逐字段相同。新运行evidence checksum `1e3089d071dd49a9973651cb376dbe71e1d66ce690335568109e57da95aa5dc7`。旧全量hash不能反推哪个配置键变了，未把差异认作心跳或自动忽略；先做最小只读归因，影响边界未确认前不开始业务移植。

拟定的下段职责：`companion` 域持有连接密钥发行/轮换/撤销、当前权限约束的机器鉴权及报告协议，单向调用connections；`connections` 继续持有目录Snapshot/Refresh。纯报告规范化不引用框架，原生存储参与同一连接事务；不将客户端报告的complete值当作供应商/银行/映射已读取的证据。真实Windows二进制来源尚未核验。

旧legacy发送只带裸log_id，ACK缺少新账本的attempt凭据，不能直接接入而关闭第八批fencing。计划先迁身份/心跳/目录，不注册发送或ACK消费者，不造一套放宽保护的兼容入口；真实发送必须先证明客户端可支持新凭据或可信兼容句柄。旧普通requested刷新合并同ID，但leased时再次请求实际是旧行failed/ERR_REFRESH_SUPERSEDED、新ID；完成/到期另保留requested_at>started_at的防御性重取规则，不能只看其中一段逻辑迁移。

### 基准缩小与本批范围

恢复第七批最初 v1 SQL 的精确文本后，只读重取同一投影：Web/Worker 各60条、摘要均为 `67c022c7edb856033715c51bacf02b22`，与原v1相同。调查证据checksum `d313d78c34621288ae9e6d31dad2006ca4ac2a7615a495e8eed51c216840b28f`。它覆盖token摘要及所列固定配置键，但没有user_id/express_root，不可推断所有归属/目录不变；updated_at也不是完整更新轨迹。因此仅放行无旧数据迁入的原生凭据生命周期、内部token认证，以及独立于ERP配置的无照片scan导出。全量v2差异仍HOLD，未将其覆盖或重命名为通过；心跳与目录采集仅保留设计。详见[第九来源审查](cowork-backend/ninth-source-review.md)。

所属App均为pearnly_cowork。companion持有新的原生凭据数据，公开仅issue/revoke/status管理RPC；内部authenticate/revalidate不是机器HTTP入口，不设置session user。stocktake.export持有纯字节导出逻辑，公开GET只读下载使用既有stocktake角色、购买功能与公司范围；无新导航、前端、外部网络或ERP写入。旧盘点route完整路径还含照片SQL读取，本批明确不包含照片，不能将无照片切片说成全导出迁移。

### 本地候选与目前验证证据

仍使用第八批候选镜像 `sha256:d5c874943dc8394a011d8c2b0c795325256d697567a2706c9ed5e25f27fc93f9`，Python3.14.7/Frappe16.33.0/openpyxl3.1.5。测试前复核本任务专有compose/context、源码挂载、Site与数据卷、internal网络；无端口、无外部凭据、无worker/scheduler。没有切换共享DMS样板。

- 新 `cowork-backend-companion.localhost` 安装/迁移通过；既有push Site升级和重复迁移通过。快照新增纳入第八批6张连接/推送表，21数据组（20组表/用户字段与56份原件字节摘要），439条原有记录保持一致：`a06c5fda8066f462bbe6d182020119887042b096cfb986182c06c7050f6b9c0c`。脚本只输出数量与hash，不输出密码或凭据；新空表不是旧数据迁入。
- 候选无网容器完整 `test_cowork_*.py` **689项通过**（652旧项+16身份合同+11基准调查+10导出），无skip。日志 `/tmp/pearnly-cowork-migration-20260920/ninth-unit.txt`。没有因为新增功能重复旧ERP外推差分或付费模型测试。
- 固定源码真实函数差分：盘点84份工作簿/17,524单元格值、类型和样式差异0；身份244组hash/生成token/端点查询判定一致，查询依赖为合成端点，不假称真实旧库鉴权。源码完整hash在执行前核验，容器无网。最初直接绑定host `/tmp` 在Colima VM中不可见，FileNotFound后改stdin完整源码传入并核hash；未改业务逻辑或删除断言。
- 盘点原生14项回归、新导出3项通过；真实WSGI完整101条、作废/关闭、跨公司/权益/用户禁用/legacy拒绝、公式样式用户串、响应no-store/nosniff与附件格式、实际Frappe每Site deferred Access Log队列均验证。初次把原生deferred日志误当立即落库，修正为读取真实队列断言并补记录task ID；不为了测试启动scheduler。原有连接21项回归通过。
- 新增23条术语，仅Cowork四语包更新；32个所需msgid由Core+Cowork可解析，无依赖Control。实际新Site执行en/zh/th/ja共92次新增译文查询，均与TSV一致。术语表校验通过；strict全仓仍旧147项/43文件/135条，HEAD/候选归一行号差异0，未扩大忽略，不称全仓语言过闸。

- Companion最终**20项真实Site测试通过**（5.662s）；加连接21、盘点14、导出3，本批合计**58项原生测试**，重复运行不叠加。包含两数据库进程在旧RR快照后竞争同/不同key签发、并发CAS reset、当前授权撤销、公司/客户停用、总闸、native CRUD/DocShare、重放无token、无Version、回执接近1MiB后仍可撤销、WSGI真实API及exp_无法充当普通RPC身份。没有把假context校验当作真实Windows或跨Site物理设备验收。
- 初轮身份原生18过2失败：测试错误要求额外private头（实际共享保护是更严格的no-store并带no-cache/nosniff）；故障注入按dict新文档is_new判断未触发原生insert。修正为检查实际三个响应头；在第二代Credential.before_insert确认旧代已撤销后注入异常，并断言注入确实执行、回滚后旧token可用且没有新代。没有删除回滚或保密行为断言、没有修改共享File逻辑。最终结果见 `ninth-credentials-native-final.txt`。
- 测试后对新身份Site重复原生migrate，10条Credential全字段摘要保持 `d06c107b35228893c960143ff64c65e202c1b6626cf72e15a2ee31c18bf2d462`，Version记录0。原生迁移没有转换线上密钥、用户或旧ID。
- 独立审查指出并修复两处实现差异：旧推送总闸遗漏；凭据请求回执占满容量后撤销会被回滚。当前发行/认证/复验遵守总闸，首次撤销保留1KiB回执空间，两者纯合同和真实Site验证通过；未以关掉幂等或删除审计方式回避。见[审查记录](cowork-backend/ninth-source-review.md)。

### 交付复核与余项

最终旧来源checksum `15e7fd1ed39cb0c597f6de5431ac02e2e2bddd2b02a30e1434647718f5228e3b`，第九start/delivery源码与镜像一致；runtime checksum `29c1276b7f64d97963fc916d0fd0aab2d803041cecc561d532f9a59e56523678`，本批首尾settings_unchanged=true/observations_changed=false。精确v1 delivery checksum `a2239ae9c66d6c05ef32d02b7b057d1c856a63a3a6ef4981b05f514092c90802`，与第七v1及第九补采一致；完整v2相对第八的差异仍HOLD。六份脱敏原证据归档在evidence；此处是canonical内容校验值，不是JSON文件字节hash。不能用本批首尾稳定抹掉跨批配置差异。

目前可继续的下一步先解决目录配置来源范围，随后实现受限机器身份桥、心跳/目录报告和有代际凭据的运输/ACK。旧数据和owner映射、实际Windows来源/客户端兼容、MRERP、未知回读恢复、History投影与通知、LINE/WeKan、盘点照片及legacy/导入删除、对账和剩余导出/归档仍未完成。前端未开始，尚未到接前端讨论点。照片与legacy导出明确拒绝，不把缺失藏在空页面或完整导出声称里。

本轮仅本地实现/测试通过，CI、部署、外部业务结果、Windows/Excel真机及用户验收均未执行。未合回主线、未推送、未部署；另一个窗口的主目录继续前进且末次核对干净，HEAD为 `12176e9e09d579e71935ef456900870e5c85d1f1`。它新增DMS预热队列修复/记录，未引入本批架构或底座要求变化；本任务没有修改、合并或重启它。独立工作树保留用于后续迁移，不能将两分支称为已统一。

第九批收尾：所有测试结束，本任务bench/db/redis三个容器已停止，隔离Site/DB卷及合成数据保留。架构检查0违规、变更Python语法与diff检查通过。通过共享暂存快照钩子保存独立本地提交；不清理或接管其它任务资源。
