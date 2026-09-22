# 第五十二批：账套主体批量导入

开工 `56365403a5e231296f3a29ea3f45bade535d1284`，续接 Cowork 专属工作树。
用户要求先搬完主要后端，再一页一页接前端；本批不做前端、票据改挂/解绑、合并、推送或部署。
开工生产身份采集 `baseline-fifty-second-start.json`，源码仍 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`。
状态：本地实现与验证完成，机械抽分 `d906f9a0cde70c6dd2a9ba2cd7e416e58f786e18`，
业务提交 `bfea28e1f3fec3b91b9f6e1a65d9a14cbb813328`。未合并、未推送、未部署。

## 归属与设计

- 固定源 `routes/client_import_routes.py` → `routes/workspace_routes.py::_create_validated_client`
  → `services/workspace/store.py::create_workspace_client`，实体是账套主体，不是买方。
- `masters` 域新增独立 `workspace_api` / `workspaces` / `workspace_fields` / `workspace_import`。
  旧 `masters.api.create_workspace` 保持入口但共用校验；已有买方导入 RPC 完全不改义。
- `Cowork Workspace` 增加导入来源已有的 address / branch / phone 字段，仍是本 Site 资料，
  不开外部账套、不设置连接、不迁 VAT 开票/个人主体/财年/单号前缀等范围外功能。
- CSV/XLS/XLSX 字节读取共用既有叶子解析器：从 reconciliation 机械移入 `tabular`，
  原路径保留显式薄导出，内部消费者改新路径；masters 与 reconciliation 单向依赖 tabular，
  tabular 不反向依赖业务域、数据库、Frappe 或网络。解析策略变更与机械移动分别记录。
- 预览零写；提交服务器重新判定，按行 created/skip/error。单建与批量写同一道字段/税号闸，
  公司级税号防重实际按 Site（旧 tenant）串行当前读，不借用买方表。
- 私有原生导入回执按当前操作者+request_key 幂等，冻结指纹与结果，连无税号行也不能因重试重复创建。
  同 key 不同内容拒绝；已有税号的新批次 skip；不自动合并已有重复账套。
- M1 工单泰文名称强制闸按第五十一批范围决定不迁；不是删除已有身份、权限或写者保护。
- 所有入口受 Cowork Site + master_data + Cowork Admin 约束；没有菜单或页面，点击去向不适用。

## 接口与有意差异

`pearnly_cowork.masters.workspace_api` 三条仅 POST 的原生 RPC：

- `parse_workspace_import()`：multipart `file`，文件最多 8 MiB，首个非空工作表、前 15 个非空行找表头，
  最多读取 2000 数据行后过滤汇总，再按来源规则最多给 500 行；返回 rows/header/matched/truncated 与 preview。
- `preview_workspace_import(rows)`：行字段只允许 row_index/name/tax_id/address/branch/phone；有效、已存在、错误三态，零写入。
- `commit_workspace_import(rows,request_key)`：当前 Site 与当前操作者，最多 500 行及 8 MiB UTF-8 规范化 JSON；
  request_key 非空且最多 128 字符。返回 results/total/created/skipped/errors/replayed。

已有税号、新批次重复税号均 skip，不覆盖公司；同一批里第二次遇到税号也 skip。
同操作者同 key 与规范化内容重放原回执，计数是原批次计数；同 key 不同内容拒绝。
**没有税号的新 key 会允许新建另一家公司**，不按名称擅自合并；前端将来应给一次确认固定一个 key，网络重试复用它。
确认后修改错误行要新 key；原回执 immutable，不靠修改/删除回执重新执行。

有意变化及边界：

1. 原 tenant 对应当前 Site，来源功能权限转为原生 Cowork Admin + master_data；原生创建/修改与单建也执行同字段/税号闸。
   没有新功能码/购买项/菜单；不会创建用户授权、ERP 连接或云端租户。
2. 名称 200、地址 500、分支 120、电话 50：超长拒绝而非静默截断；税号保持现有原生字段 40（旧 DAL 截 30）。
   导入税号可空，非空须能归一成 13 位；单建已有税号输入口径不额外收窄。空分支默认 `สำนักงานใหญ่`。
3. 活跃公司防重按 13 位归一税号比较；原侧是文本完全相同才重复。现存历史重复不自动合并，
   不阻止无关字段修改；新建、改税号、重新启用必须查重。DocType 元数据行作 Site 事务锁，随后当前读，避免旧快照绕过。
4. 源码旧提交本身没有无税号的请求幂等；本批加原生私有回执，与新增公司同事务提交。
   预期逐行校验失败回滚该行；权限/死锁/未知异常向外传播，整请求由原生事务回滚；独立 RPC 仅死锁最多 3 次重试。
5. 二进制按字节签名识别，不将损坏 XLSX/XLS 再当 Latin-1 CSV。XLSX 预检最多 1000 个条目/64 MiB 声明展开量；
   本导入调用共享读取器时限制 250000 单元格、256 列/100000 行；银行/GL 不传限额，原行为保留。
   限额/损坏拒绝不算来源差分一致；既有 CSV 编码顺序 cp874 在 GBK 前，GBK 中文可能被读成泰文，未悄悄改规则。
6. 不迁未消费的 suggested_period、M1 工单泰文闸、VAT/个人主体/财年/单号前缀；不改票据改挂/解绑的不可变契约。

## 来源与验证

固定源码文件摘要在 `evidence/fifty-second-local-validation.json`：三条路由/存储来源及 summary/client_import/bank_table_io 解析链。
开工与交付线上身份都为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`，首尾及与上一批比较 PASS。
证据 envelope sha256：开工 `86b16b306d61d18ae010665a4d0ea86d5c222301c18218296eea475d5ae45d58`，
交付 `b0abb5a2e26cee12b1bdf5272fd740b37c796e164ee6e7bd65cace642cced6f8`。
推送运行投影与第五十一批比较 settings_unchanged=true / observations_changed=false；
第五十一批相对第五十批的 endpoint configuration_digest 变化原因仍待查，本批不读它，也不覆盖旧问题记录。

| 验证 | 结果 |
| --- | --- |
| 新离线字段/文件解析 | 9 通过，CSV/XLSX、汇总过滤、500 行、文件/展开/单元格限额、关闭失败保留成功结果 |
| 固定源 AST 对照 | 432 组零差异；只执行指定纯函数，无旧应用导入/网络 |
| 共享读表回归 | 银行 36、GL 与 intake 47，通过；总计离线 92 |
| 共享读取链固定源回归 | 银行 227；GL 225、普通文本 187、KBank 5、PDF 4，均零差异 |
| 原生导入 | ack 升级 Site、新装 workspace Site 各 17 通过，含 500 行、防重、范围/权限、字段、失败回滚与有界重试 |
| 原生 HTTP | 两 Site 各 10 请求通过：匿名 3×403、GET 写 3×403、文件→确认→回放、私有回执列表拒绝 |
| 真实两进程并发 | 同税号不同 key 仅 1 created/1 skip；无税号相同 key 仅 1 家、1 次回放。双方先建立旧 RR 快照 |
| 受影响原功能 | 主数据 22 + 原后端 21 通过；原主数据 14 个匿名入口均 403；跨 Site 独立数据库通过 |
| 原生结构 | 新装 Core/Cowork PASS；ack 升级及重复升级 PASS |
| 升级保留 | 首轮原 269 公司、26 票、偏好及分类/记忆摘要不变；重复升级含新联系字段及 5 条回执的摘要全部不变 |
| 静态 | architecture 0 违规；实际暂存快照 hook PASS；i18n-build 1561 条 PASS；diff check PASS |
| 翻译报告 | REPORT-ONLY 428，比前批 +3 是汇总行中文匹配词，不改规则或扩大忽略；已有四语词库欠账仍在 |

测试命令曾误用不存在的 GL/intake 模块名导致导入失败，核对实际路径后 47 条全部通过；
主机没有 openpyxl，依赖解析检查改在锁定 bench 内执行。未删断言、未跳过失败、未绕过 hook。
XLS 使用既有 xlrd 回退器且依赖存在，但本批没有真实 XLS 字节样本验收；不能把 CSV/XLSX 通过说成所有格式均验收。
全部业务数据为隔离 Site 合成数据；没有浏览器/用户验收，没有付费 OCR、LINE 或外部 ERP 调用。

## 交接保留与下一步

本地主线回读 `466beae046b580bb7eab303893cf20bb9498cc4b`，远端 main `ac33e0c7362915ae314295cb26f7748cb5589de0`，
远端任务分支不存在。代码提交时 task 相对 main 有 69 个独有提交，未获合并/推送授权。
仅本地提交不代表全仓统一。另一窗口 ERP 的工作树和文件未改。

| 资源 | 所属、用途、保留原因和复核点 |
| --- | --- |
| `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly` / `codex/cowork-backend-migration` | 本 Cowork 迁移；未合并独有代码且停止的 lab 仍挂载；下批开工及获授权整合时再核对 |
| Compose project `pearnly-cowork-backend-lab` bench/db/redis | 本任务，收尾 inspect 均 exited；源码只读挂载未改变；后续前后端接线仍需复用，不自行删容器和数据库 |
| 卷 `pearnly-cowork-backend-lab_db`、`pearnly-cowork-backend-lab_sites` | 本任务持久 Site 与升级证据；含新增 `cowork-backend-workspace.localhost`，最终数据处置获授权后再删 |
| 卷 `71640b5a012f8c99c84b49a27f154d78252b355a87ff8dadfa2e41fc3f3db492`、`cf5ad7fbfd49e29c178da9c101cbb13b78e3bc64649569a08352fb6d2e9fd269` | 本 lab 日志/Redis，保留给后续验证；最终关闭迁移 lab 时精确复核 |
| `/tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b` | 固定只读来源，后续验收和差分用；迁移结束前保留 |
| 本树既有 apps/scripts/tests `__pycache__` | 旧批次生成，未证为本批新增；最终整体收工再逐项核对。新 workspace/tabular 模块缓存未发现 |

无符号链接、无仍运行的本批宿主验证进程；本批未删除任何工作树、分支、数据或临时文件。
这是交接保留，不是“全仓清理完成”。按当前范围主要必搬后端缺口已补齐；
票据改挂/解绑须先定跨域规则，可选器件/真实外部验收、最终合并另有边界，前端继续按用户要求之后一页一页接。
