# 生产部署 afc615bb（加成员修复、文案整改、第 12 轮拖动、统一原件层等）

- 依据：用户 10-06 起的按批次自主部署授权（记忆 autonomous-batch-deploy）；10-17 起「没客户、进维护立即部署」。10-18 用户要尽快能在 testcowork 加第二个成员（多电脑第 7 批要用），这批在成员修复合入后立即构建。
- 窗口协调：「消息含义理解」窗口做 Cloud Run 新版、ERP 站点配置、全平台 CC 引擎下发（apply_to_all），都按约好的先后在激活前完成；测试窗口复测完第 12 轮 D15、置顶顺序修复后不占 lab。
- 上一版生产：`ec90fdfc`（镜像 ebba5a40，10-18 00:01）。

## 内容（main `909b858c` → `afc615bb`，另含此前已在 main、未部署的提交）

- 统一原件层 ADR-024（`669427ca` 等，「消息含义理解」窗口合入）：三产品所有入口按识别路线存甲骨文 / 谷歌，生产只留登记；改了 pearnly_core hooks。
- 全平台 CC 引擎设置（`19a26d22`）：引擎 / 模型 / 密钥全平台一份，开户单与租户表单不再填。
- 开户代理维护容器（`f2438353`、`ae7cd867`）：已对外站点的每一步、开通流程的 publish_site 做完就收；激活产品时收本产品的维护容器。
- ERP 原件上传失败文案书面化（`10992c21`）；只构建不发布的 CC 桌面版测试包流程 `companion-build.yml`（`909b858c`）。
- 文案存量整改（`docs/work/2026-10-17-copy-stock-review.md`，用户 10-18 同意第 3–14 节、当晚特批「涉及到文案的今晚特批，但是不用有口语」）：三个真缺陷（改密码页四语与出错原文、CC 工具步骤不再显示给模型的英文指令、DMS LINE 快捷按钮超 20 字）、H1（会话主人停用后不执行工具）、DMS / ERP / 共用层 / 控制面 / CC 桌面版 / CC 网页（i18n.ts 以外）共 216 行，闸门收窄与盲区（含合并提交只查 main 上还没有的行）；与多电脑、第 3 期冲突的 138 行延后。
- F7：产品站点每次 migrate 关掉系统设置 allow_error_traceback（`pearnly_core.desk.error_detail`，用户 10-18「关掉」）。
- 第 12 轮：自写拖动替换 dnd-kit（`docs/work/2026-10-17-drag-engine.md`），含审查补修、D15（被拖项消失即取消、键盘拖动只认把手上的键）、删 `@dnd-kit/*` 依赖。
- 加成员修复（`docs/work/2026-10-18-member-identity-pool.md`）：控制面成员登记表 product 只收 erp，Cowork 租户加成员全被表校验拒绝、只回「请稍后重试」→ 收 erp / cowork；建号与改密码按站点实际签到的账号池走并在登记上记池（testcowork 签到业务池）。
- 多设备置顶顺序被旧列表覆盖（`docs/work/2026-10-18-cc-pin-order-race.md`）：按数据收拾只在依据比布局新时才存。

## 验证

- 本地全闸门 92 步（`1dda6417`、`afc615bb` 各一次）全过；分支 Development checks（`1dda6417` run、`afc615bb` run）通过。
- 候选离线检查：本机 `cand-local.sh offline`（生产镜像 ebba5a40 + 候选代码挂载）在 `1dda6417` 上 72 步全过；测试机原生（同一脚本，挂载核对 WT）在 `afc615bb` 上 72 步全过。文案整改那次曾被 `test-dms-line-language.py` 拦下（菜单标题统一成品牌名后中英 altText 相同），改为按正文提示语判语言后通过。
  另：`~/pearnly-devtools/cand-run.sh` 的挂载 sed 已匹配不上现在的候选脚本（第一行计数 0，测的是镜像里的旧代码），已标失效。
- 测试机 lab：第 12 轮 B / C / D 段与回归通过，A14 帧测拿起 58→11ms、落定 34ms（仍高于目标，第 13 轮做）；D15 真页面与复刻页复测通过；置顶顺序修复真页面复测通过（`round12/第12轮回报.md`）。
- 构建 run 39068327884（offline、flow、snapshot 三条候选车道全过）→ `sha256:49cbba82cfc12716e6972e5cd1597bccaf63526e10aa00acbfbc565b5e357781`；stage run 39096219297 成功。

## 部署（泰国时间 10-18 11:11–11:22；服务器日志为 UTC 04:11–04:22）

1. 「消息含义理解」窗口：Cloud Run `cowork-ocr` 新修订版 `cowork-ocr-00075-pos`（镜像 afc615bbd8b2），`--no-traffic --tag` 冒烟（不认 ERO- 的旧版回 invalid_request、新版回 original_missing；只带引用的对账请求回 original_missing，无付费调用）后按名字切 100%，标签已删，环境变量无评测开关、minScale 0；回滚 `update-traffic --to-revisions cowork-ocr-00061-c72=100`。ERP 组 common_site_config 写 `pearnly_cowork_ocr_remote`（同 Cowork）与 `pearnly_cowork_originals_bucket`（备份 `common_site_config.json.bak-originals-20261018`），mrerp / testerp clear-cache 回读。
2. 服务器脚本 `/opt/pearnly/state/deploy-afc615bb.sh` 第一段 `PHASE=sync`：同步部署工具，`check-deploy-image` 一致（UTC 04:11:59）。
3. 「消息含义理解」窗口 apply_to_all：6 个云端站点 set_assistant 全部 done，回读 anthropic-managed-agents + claude-haiku-5-5、密钥有值；新开户代理做完即收，生产维护容器 0（之前留下的 pearnly-maintenance-dms 也被收掉）。
4. 第二段 `PHASE=activate`（日志 `deploy-afc615bb.log`，内存采样 `mem-afc615bb-activate.log`）：ERP 04:14:21、Cowork 04:17:07、DMS 04:19:23 各 "activated with verified image"，基础层 04:21:17 "Foundation activated"，三产品 `bench --site all clear-cache`，04:22:39 完成。内存：开始可用 28.9GB，产品之间最低 26.8GB，结束 26.7GB，swap 从 4.2GB 降到 0.5GB。

## 回读

- 生产 frappe 容器全部为新镜像，三个 ocr-worker `FRAPPE_BACKGROUND_WORKERS_NOFORK=1`。
- 六站 maintenance=0；Error Log（站点时间 04:14 起，比部署早 7 小时）六站 0；公司三表、会话五列、patch18 照旧在。
- 基础层：`Pearnly Member Identity` 有 `realm` 列，product 选项 erp / cowork。
- 六个产品站点 `allow_error_traceback` = 0。
- `pearnly_core.originals.store.where`：erpcowork = gcs、mrerp = gcs、testerp = oracle（testcowork 在 lab，生产这份为 Claude / oracle）。
- 非生产容器 0；mariadb 767MiB / 8GiB；mrerp、erpcowork、ris ping 200，fatcc.com 403（既有放行规则）。

## 未完成 / 后续

- **加成员真机验收**：用户明天在生产 testcowork 加第二个成员（testcowork 现指向 lab，要先 lab-stop，加好后 lab-start REFRESH=1 挂多电脑开第 7 批）；回读应为登记 2 条、新登记 realm = pearnly-business、新号能登录 testcowork。
- 统一原件层第 5 条（新原件 gs://、对账临时文件 CWS-…bin、ERP 新附件 ERO-…bin、`/private/files/<登记>.bin` 跳签名地址）没有实际上传去验，等有真实上传时抽查。
- 部署前要人工核对、尚未做：CC 网页点一次「拒绝」展开步骤看是否显示「已拒绝此更改」；DMS 部署后请用户用手机走一次「凭证与付款方式矛盾」卡片（真 LINE 由用户手机走、服务器核对）。
- 文案整改里 CC 桌面版改的两条随 1.7.0 一起发（不单独发版）。
- 下一轮：H 线、第 13 轮、B1 标准层已叠成一条线在 lab 测；多电脑（含桌面版轻量化重构）等第 7 批；第 3 期 E1 修法待施工。
