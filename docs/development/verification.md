# 按影响面验证

一份结果只证明它实际覆盖的代码、环境和场景。没有新修改、失败或新证据，不扩大或重复验证。既有有效强制检查仍需完成；本规范不把未接入的工具称作自动闸门。

## 选择矩阵

| 改动 | 最低需要的验证 | 何时扩大 |
| --- | --- | --- |
| 文档、AGENTS、模板 | diff、链接、命令/路径存在性、规范冲突 | 增加了脚本或执行配置时，验证其真实调用 |
| Python 纯逻辑 | 适用配置的语法/静态检查，受影响成功与失败用例 | 引入 Site、数据库、缓存或外部 I/O 时进入对应层 |
| Frappe 接口、权限、共享 core | 隔离 Site 内行为；未登录、无权、跨租户拒绝；受影响调用方 | 共享身份/会话改变时覆盖相关产品入口，至少包含已跑通 DMS 链路 |
| DocType、fixtures、patches、安装 hooks | 新 Site 安装、已有测试 Site 升级、重复 migrate、数据/用户设置保留 | 有数据风险时做新鲜备份及独立 Site 恢复验证 |
| Vue 页面、交互、接口字段 | 前端 build、翻译检查、受影响入口的浏览器操作和服务端回读 | 手机布局、语言或 LINE 行为改变时增加相应设备/语言验证 |
| LINE / DMS 状态流转、队列、提交 | 定向链路、重复事件、nonce、并发/幂等、权限、失败与重试状态 | 需要外部效果时在已有明确授权下真连并回读结果 |
| 任何旧功能迁移 | 开工与交付前旧生产版本回读、源码映射、行为及依赖增量核对 | 线上相关行为变化时同步增量并复验；见 [线上基准](migration-baseline.md) |
| Compose、镜像、底座依赖 | 配置解析、锁定版本/架构核对；受影响启动与持久化 | 底座升级加入安装、迁移、恢复及 DMS 关键回归 |
| 检查脚本、CI | 语法、正常路径和关键拒绝路径，核实作用范围 | 不为每行配置写一份镜像测试，不触发业务全量作为默认 |

格式按各 App 的 `pyproject.toml` 和已有配置；不要跨仓复制 Ruff 版本后批量改写全部文件。存量问题记清基线和影响，新增回归必须处理，不能假借存量问题忽略本次失败。

## 现有入口

### 静态与前端

在工作树根目录运行：

```bash
git diff --check
git diff --cached --check
python3 scripts/i18n-build.py --check
python3 scripts/check-i18n.py --strict
python3 scripts/check-frontend-i18n.py --strict
npm --prefix frontend run build
```

按矩阵选择命令；上面的翻译检查不会改语言包。首次安装或锁文件变化时用 `npm --prefix frontend ci`。`build` 自带 `vue-tsc --noEmit`，只查类型可单独用 `npm --prefix frontend run typecheck`。

构建输出在 `apps/pearnly_core/pearnly_core/public/pearnly-app/`，是被忽略的生成目录。正式构建需确保镜像包含对应版本的产物；不能把旧仓的 `static/dist` 提交规则搬过来。前端开发入口见 [前端说明](../../frontend/README.md)。

App 下虽然有 `.pre-commit-config.yaml`，本工作树根目录尚未配置统一 hook。远端其他分支已有手动镜像构建/投递工作流，这与统一 PR 检查不同。不能认为进入 App 目录就会自动检查整个单仓，也不要未经核对运行带 `--fix` 的全仓钩子。

### Frappe 与 DMS

新增框架集成测试优先放在所属 App 的 `test_*.py` 中，使用锁定版本的 Frappe 测试机制；在已配置开发依赖的隔离 bench 内用 `bench --site <测试站点> run-tests --module <真实测试模块>` 选择范围。占位符不是现成模块，不能记录成已执行。[官方测试文档](https://docs.frappe.io/framework/user/en/testing)

迁移中的既有 sample 脚本继续保留，不为统一目录一次性重写。以下是选用索引，不是自动执行列表：

| 影响范围 | 本仓入口 | 执行前提 / 证据边界 |
| --- | --- | --- |
| DMS 客户端纯逻辑 | `scripts/sample-verify-dms-client.sh <组>` | 假 transport、不连外部；包装器仍使用指定容器及其中安装的代码 |
| 订车提交登记 | `scripts/sample-verify-dms-booking-attempt.sh <组> <测试Site>` | 真实 Site 会话和 Redis；会改本地测试状态 |
| LINE 入口路由 | `scripts/sample-verify-dms-entry-e2e.sh <组> <测试Site>` | 出口和两处入队均替换，真实本地会话；不是 LINE 真机 E2E |
| 网页编辑器 API/票据 | `scripts/sample-verify-dms-edit-browser.sh <组> <测试Site> <端口>` | 本地 Site 与 HTTP 断言；已有脚本假定该测试站点未配置 LIFF |
| 编辑器页面 | `scripts/sample-verify-dms-edit-page.sh <组> <测试Site> <端口>` | 先读脚本，核对真实 DMS 主档读取、测试草稿及消息出口；不默认离线 |
| 查询/审批/其他 DMS 模块 | `scripts/sample-verify-dms-*.sh` 中对应单项 | 先读选中脚本和被调用代码，逐项确认副作用；禁止 glob 批量运行 |

测试前必须确认容器运行或挂载的是本次工作树的代码；脚本从本树送进容器，不代表容器中的 App 也来自本树。记录镜像 ID、挂载来源及 Site。多个脚本共用 `/tmp/dmscheck`，未经隔离不要并行覆盖同名脚本或争用同一测试账号。

新建或修改验证脚本时明确标出：使用的环境、数据修改、外部调用、退出条件、清理范围。异常、缺依赖、未跑到断言都应失败；不能只打印 `PASS`，也不能只见 `RESULT` 就忽略后续错误。

### 运行配置

`bash scripts/stack.sh config --quiet` 用于配置解析，`bash scripts/stack.sh ps` 查看样板服务。完整 `config` 和容器环境可能包含凭据，不作为未脱敏的提交或聊天证据。

`scripts/dev-reload.sh <组> --sites <测试Site>` 会清缓存并重启运行组；即使指定 Site，同组服务重启仍影响其他 Site。结构变化才用 `--migrate`，先准备数据保护；默认不执行 `all --migrate`。

## 四类副作用

1. **离线**：静态检查、假 transport、无网络单测，可在本任务环境直接执行。
2. **本地数据写入**：Site 测试、草稿、队列、fixture，须确认隔离数据与恢复方式；不借测试清空共享数据库。
3. **外部读取**：登录、查主档也可能挤掉共享账号会话或消耗配额。确认任务范围和账号占用，按读请求安排。
4. **外部写入或付费调用**：DMS 建单、推送 LINE、真实 OCR、账号/渠道配置等只在明确授权下执行；记录目标、操作和回读方式。

已发现 `sample-verify-dms-booking-real.sh` 会真实建订车单并挂附件。`*-real*` 通常需要额外核对，但没有 real 后缀也不能判定安全。预览卡、日志或 HTTP 成功不代替外部系统记录。

## 每次怎样留证据

记录：时间、源码 SHA（未提交变更另记差异）、环境/数据归属、命令或操作、预期、实际、退出码及未覆盖项。日志/截图存受控位置并脱敏，索引写工作包。

分别标记实现、本地验证、CI、部署、外部结果、用户验收为“通过 / 失败 / 未执行 / 不适用 / 阻塞”，并给依据。只有用户确认才记录用户验收；一个层级通过不自动推进下一个层级。历史通过保留原日期，不改写成当前版本通过。
