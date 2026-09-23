# 第五十批：重复发票提醒与分类边界

开工基线 `3acb28e9e5f6176fbb47603c7564a08c23dc5fd5`；Cowork 专属工作树。
状态：本地实现与验证完成，代码 `1ada03d7aced7f70baa0d612dbee82f54e16fea7`。
线上来源开工/交付回读见 `evidence/baseline-fiftieth-{start,delivery}.json`，
均为 `ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188`；推送配置变化单独列在下文。

## 当前用户决定（2026-09-22）

用户要求“只用于展示的就不要了”。分类用途需逐项区分：

- 不迁：独立的 `category_tag` 展示标签、`/api/categories` 的补全列表与供应商映射数量、
  仅用于展示的分类分布统计。
- 保留：票面 `fields.category`、复核表导出/回导中的分类，以及既有 Express 分类到真实科目码
  的映射。`push/express/common.resolve_account_sourced` 先按分类命中映射，再回落默认科目；
  默认来源会留下 `account_review`。因此不能把分类值全部删除，也不能声称它只影响展示。
- 自动分类树与供应商分类记忆本批暂不建设。它们会改变 `fields.category`，从而可能影响
  Express 选科目，并非全是纯展示；“暂缓实现”不写成“用户要求全部删除”。
- 商品/费用、现金/赊账的供应商过账档案属于另一套规则，维持既有实现。

## 迁移合同

App `pearnly_cowork`；纯判据在 `contracts/duplicates.py`，存储与权限在 `history/duplicates.py`；
入口 `history.api.duplicate_setting/save_duplicate_setting`，与 `storage.create_recognized`、
`recognition.persist_batch` 集成。无新跨域依赖，无外部副作用。

来源：`services/ocr_history/queries.py::check_duplicate_invoice`、
`services/user_settings/store.py`、`services/ocr/recognize/persist.py`、
`routes/settings_routes.py`。旧代码实际行为：先比票号（忽略大小写）；未命中再比日期、
卖家名（忽略大小写）和金额，即便当前有票号也走第二层。命中只提醒，不阻断落库。

偏好按当前用户保存于 Frappe 原生 DefaultValue，默认开启，无指定他人用户的参数。
识别创建检查当前作者在当前公司下有权读的记录；指定客户时含该客户与未归属行。
手动录入与复核不新增检测，保留识别时的证据。相同请求重放不重复检测。
原生 History 新增只读检查状态、级别、私有引用；仅 RPC 按当前权限投影匹配记录。
依赖查询失败记 `unavailable`，不伪装成无重复；权限失败与数据库死锁不吞掉。

有意适配：使用 Decimal 精确比金额、公司/客户原生授权范围、Site 隔离；
旧瞬时返回的警告改为持久标记。历史记录默认未检测，不倒查历史、不改原票、
不代替请求幂等或财务去重约束。并发不同上传之间不保证互相看到未提交记录。

## 验证与交接

证据汇总：[fiftieth-local-validation.json](evidence/fiftieth-local-validation.json)。

| 检查 | 结果与范围 |
| --- | --- |
| 纯重复判据 + 原 history 合同 | 6 + 20 通过 |
| 固定生产源码差分 | 1,120 组，0 差异；实际提取旧函数及公司/客户 SQL 限制执行，不以手抄期望代替来源 |
| 既有 Site `cowork-backend-ack.localhost` | 13 条新业务检查通过，10 个原生 HTTP 请求通过 |
| 全新 Site `cowork-backend-duplicates.localhost` | 原生新装 Core/Cowork 成功；同样 13 条业务检查与 10 个 HTTP 请求通过 |
| 既有后端回归 | 21 场景通过；首次 20 通过、跨 Site 用例因旧备用 Site 不存在失败，配置本批新 Site 后只重跑该用例通过 |
| 识别持久化与执行回归 | 原 invoice 集成 6 通过；真实执行编排接假提供方/计费的成功、丢失计费回执、历史事务中断恢复 3 场景通过 |
| 原生升级与重复升级 | 两次 migrate 前后，原票关键字段与个人开关摘要各自一致，见下列摘要 |
| 架构 / 词库 / 补丁 | 架构 0 违规；`i18n-build --check` 通过；`git diff --check` 通过；`check-i18n` 仍为 report-only 377 项，不宣称翻译全部完成 |

升级前后摘要（两组间运行 HTTP 检查会增加明确的合成记录和偏好，不能拿两组互相比）：

- 首次：history 8 / preference 1，`bed7792bffe68d56a3198af164825c3fefe247dfe6e8e71ab2f5609a60ed9b6c`。
- 再次：history 10 / preference 2，`828a992150dbf8d356920e1b2e702eebdb8034312497c7a8b44fe7e0fcd1bad5`。

新业务覆盖默认开关、个人隔离、严格值域、公司/客户/作者范围、未归属行、撤权后隐藏匹配对象、
票号优先与第二层回落、零金额、手工录入不查、同请求重放、批内识别、查询失败、权限与死锁传播、
原生 CRUD 防绕过、复核后保留创建时警告。HTTP 验证 GET 不可写、匿名拒绝、偏好写后回读、
详情与列表、私有引用隐藏和原生更新拒绝；不代表前端已接好。

验证中发现并修复：Frappe 会在控制器验证前清掉低权限用户的 permlevel 1 字段，导致服务端设置的
匹配引用丢失。现在只在已有 `_CREATE_TOKEN` 能力验证通过后，从服务端 flag 恢复该私有引用；
不设置通用 `ignore_permissions`。原生请求伪造 flag/字段仍被拒绝。主机默认 Python 3.9 无法载入
既有 history 联合类型注解，改用 Python 3.14 跑该离线套件；这不是业务代码失败。

### 来源与运行配置回读

开工/交付源码身份一致，且与第四十九批交付一致；Web/Worker revision、镜像与源码没有变化。
固定 `services/ocr_history/queries.py` SHA256 为
`8dc590392f5b2898136a226ac48f3e86bc10e14eaa2d34ec4f110ddd32ebc085`。

推送运行投影比较为 **FAIL: `push_runtime_identity_changed`**，不是全通过：Web/Worker 的
`database.identity.endpoints.configuration_digest` 从 `c5882f4b8d2c4baed118699cf5869e68`
变为 `d23570c1332deff1a646c69e9a18b921`；其他身份字段相同。滚动观测中的其他适配器近期成功数
10 → 9，总量 101 不变。没有进一步读取原始敏感配置定位具体字段，不能解释为仅凭据变化。

本批重复检测来源只读用户偏好与识别历史，不依赖推送连接配置；因此该变化不阻断本批规则交付。
没有导入、改写或修正线上配置。下一批触及推送时须先调查该差异，不得复用旧的“运行配置一致”结论。

### 复跑入口

- 纯规则：`PYTHONPATH=apps/pearnly_cowork python3.14 -m unittest discover -s tests -p test_cowork_duplicates.py`。
- 来源差分：`PYTHONPATH=apps/pearnly_cowork python3.14 tests/test_cowork_duplicates_source_parity.py --source /tmp/pearnly-cowork-migration-20260920/legacy-ec2e5c957e6b`。
- Site：按总交接的 guarded lab 命令执行 `scripts/cowork-duplicates-lab-verify.py`；`--http` 为原生请求检查，`--snapshot` 为升级前后摘要。
- 跨 Site 回归使用 `PEARNLY_COWORK_OTHER_TEST_SITE=cowork-backend-duplicates.localhost`。
  `--install-fresh` 仅首次新装使用，已存在时拒绝覆盖，不能当重复回归命令。

### 交接保留

本批未合并、未推送、未部署、未接前端、未做用户验收。未调用付费模型、LINE 或外部 ERP。
当前任务仍为继续迁移，保留以下明确资源，在下批开工及最终集成前复核：

- 工作树 `/Users/skin/.codex/worktrees/cowork-backend-migration/pearnly` 与本地
  `codex/cowork-backend-migration`：未合入 main 的迁移成果；远端任务分支不存在。
- Compose `pearnly-cowork-backend-lab` 的 bench/redis/db 容器：收尾停止，仍引用本任务代码，
  留给下批复用；不删除 DB/Sites 卷与合成测试数据。
- `pearnly-cowork-backend-lab_db`、`pearnly-cowork-backend-lab_sites` 与 logs 卷
  `71640b5a012f8c99c84b49a27f154d78252b355a87ff8dadfa2e41fc3f3db492`、Redis 卷
  `cf5ad7fbfd49e29c178da9c101cbb13b78e3bc64649569a08352fb6d2e9fd269`：归本迁移 lab，
  保存原有及本批新 Site 和测试运行数据，用于后续升级回归；迁移结束、数据确认无需保留后再清理。
- `/tmp/pearnly-cowork-migration-20260920/`：固定生产源码与来源证据，迁移结束前保留。
- 既有 ignored Python 缓存未做全局清理；并非本批新增业务文件，不跨任务清理。

未删除工作树、分支或业务数据；这是“交接保留”，不是全仓清理完成。ERP 前端工作树未改动。
