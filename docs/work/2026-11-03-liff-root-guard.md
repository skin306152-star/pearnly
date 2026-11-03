# LIFF 落地地址只认站点根 + 部署回读查 LIFF（2026-11-03 事故防再犯）

分支 `pearnly/liff-root-guard`，基线 `72813762a`（origin/main）。主会话派活，范围只是代码防再犯；线上两个 LIFF 主会话已手工改回站点根，本任务不改线上、不部署。

## 事故与根因

- 现象：正式租户 mrerp、erpcowork 在 LINE 里点卡片「แก้ไข」打开 404。
- 根因一：`line_liff._usable_url` 和 `site-step.check_liff` 认定 ERP / Cowork 的 LIFF 落在本站任何子路径都算能用，
  10-05 的 mrerp `/stocktake`、erpcowork `/desk/pearnly-intake` 因此一直被保留。
- 根因二：10-23 起（dd0e09af0）LINE 链接先经本站 LIFF（`line_login.liff_hop`，`https://liff.line.me/<id>/api/method/…`），
  LINE 把这段路径接在落地地址后面，拼成 `/stocktake/api/method/…`。本次在线上复现：该地址不登录请求即 404。

## 改动

| 文件 | 改了什么 |
| --- | --- |
| `apps/pearnly_control/pearnly_control/onboarding/line_liff.py` | `_usable_url` 只认规定落地地址（ERP / Cowork 站点根，DMS `/liff/dms-booking`，末尾斜杠可有可无）；子路径、查询串都算用不了，开户 / 重排时整条改回规定。文件头删掉「mrerp 是 /stocktake 照旧」，写明 11-03 事故原因 |
| `infrastructure/production/site-step.py` | `check_liff` 同一口径：期望地址 = `https://<站点>` + `LIFF_PATHS.get(group, "/")`，不等就报 StepHint（文案格式不变） |
| `scripts/production/check-liff-endpoints.py`（新） | 部署回读与本机都能跑的 LIFF 检查，见下 |
| `scripts/production/deploy-readback.sh` | 列站点时顺带读 `pearnly_line_liff_id`，新增「LINE: LIFF endpoints and entries」一步调用上面的检查；没有任何站点配 LIFF 也算问题 |
| `tests/test_line_liff.py`、`scripts/production/test-site-step-webhook.py`、`scripts/production/test-deploy-run.py`、`scripts/production/test-check-liff-endpoints.py`（新）、`.github/workflows/development.yml` | 测试与 CI 步骤 |
| `scripts/impact/shared-parts.toml` | `control.line.channel` 登记 `line_liff.py`，预期写上 LIFF 落地地址 |

## 检查做法的选择

- 不用 LINE Login 频道接口：要 LINE Login 频道密钥，它只在控制面开户目标上（加密），生产站点配置里没有（只在 set_line 步骤载荷里经过）。
  为了回读把密钥放上生产机不值得。
- 改用 `https://liff.line.me/<LIFF ID>/`：LINE 对普通浏览器返回的页面里写着 `liffEndpointUrl`，正是 LINE 拿去拼地址的那个值，
  不要密钥，生产机和本机都能跑。它不是 LINE 承诺的接口：读不出来按问题报（退出码 1），不会当通过。
- 入口可达：对 ERP / Cowork，用 LINE 普通内置浏览器 UA 不登录请求 `line_login.enter?to=/desk`，应 302 到本站 LIFF（ID 与站点配置一致）；
  再把 LINE 会打开的两种地址（落地地址直接接路径；落地地址带 `liff.state`）各走一遍本站内跳转：第一跳必须 3xx，本站内每跳不能 4xx / 5xx，
  到离开本站（去 auth.pearnly.com 统一登录）或停在本站页面为止。用的是从 LINE 读到的真实落地地址，所以落地地址错了这里也会出 404。

### 用法

- 部署回读自动跑（`deploy-readback.sh` 第「LINE: LIFF endpoints and entries」步）。
- 单独跑（本机或生产机，只读，几秒）：
  `python3 scripts/production/check-liff-endpoints.py erp:mrerp.pearnly.com cowork:erpcowork.pearnly.com dms:ris.pearnly.com:2010411313-KiXV2tco`
  ERP / Cowork 的 LIFF ID 从站点的转 LIFF 跳转里读；DMS 没有这一跳，要在第三段给 ID。退出码 0 通过、1 有问题、2 用法不对。

### 测不到的（只能部分代替真机）

LINE App 内部行为：LIFF 是否真按这样拼地址、`liff.init`、原生相机授权、LINE 登录本身、已登录用户打开的单据页及其权限、
LINE 以后改内部规则。`liff.line.me` 页面格式变了会报「读不出」，要人看。DMS 只查落地地址，不走入口。

## 验证

| 层 | 内容 | 结果 |
| --- | --- | --- |
| 本地测试 | `tests/test_line_liff.py` 13 项、`test-site-step-webhook.py` 13 项、`test-check-liff-endpoints.py` 11 项、`test-deploy-run.py` 29 项 | 通过 |
| 反例 | 把 `line_liff.py`、`site-step.py` 换回 origin/main 旧规则：`test_line_liff` 5 个子用例失败、`test-site-step-webhook` 8 个子用例失败；检查脚本换成旧前缀口径：2 个子用例失败。恢复后全部通过 | 符合预期 |
| 线上只读实跑 | 本机跑新检查：mrerp `2010411313-5VahQwOp`、erpcowork `2010411313-xu6GcalZ` 落地地址都是站点根，两种入口地址都走到 `begin_login?redirect_to=/desk` 再去 auth.pearnly.com；ris（DMS）`/liff/dms-booking`。退出码 0。旧坏地址 `/stocktake/api/method/…enter` 被判 404 | 通过 |
| 闸门 | development.yml 全部步骤本地跑、架构检查、文案闸门、影响清单 | 见提交说明 / 交付报告 |
| 部署 | 未部署（任务限制）；回读新步骤下次部署时生效 | 未执行 |

### 共用件连带回归（`check-impact.py`）

| 入口 | 结果 |
| --- | --- |
| control.line.channel（offline） | 通过：`test_line_liff.py`、`test-site-step-webhook.py` |
| shared.shell-gate / shared.login / control.issue-and-entry / shared.assistant / shared.provisioning / shared.billing（manual） | 没测到：`site-step.py` 只改 `check_liff` 的地址比较，不在这些入口的代码路径上 |

候选离线检查：未改 `pearnly_core`；`pearnly_control` 只改纯 HTTP 模块 `line_liff.py`（无 Frappe 依赖，离线测试覆盖；`provisioning.py` 不改，流程覆盖闸门只提醒它还没有流程兜底），不跑候选车道，构建时候选车道照常会跑。
