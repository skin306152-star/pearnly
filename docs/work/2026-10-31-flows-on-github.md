# 流程测试搬到 GitHub 机器：公开仓库前审计（2026-10-31）

主会话 10-31 定：流程测试（`scripts/flows/run.py`）改在 GitHub 自带的 x86 机器上跑，靠把仓库 `skin306152-star/pearnly` 改成公开来免费。
分两步：第一步只读审计（本记录），报主会话确认后才动仓库可见性；第二步再做工作流。
分支 `pearnly/flows-on-github-ed0bb3`，基线 `origin/main` 788197a9。

## 结论

**不能直接公开。** 历史里没有仍有效的密钥，但有一位真实终端客户的完整个人信息（身份证号、生日、地址、电话），
还有两台自有 runner 会被外人的 PR 用来跑代码。这两项不处理，公开就不可撤回地出事。

| 级别 | 项 | 处理 | 代价 |
|---|---|---|---|
| 必须 | A1 真实客户个人信息进了历史 | 改写历史，或换新仓库 | 见「三条路」 |
| 必须 | A2 自有 runner 对外人 PR 开放 | 公开前删两个仓库变量、注销 4 个 runner，全部回 GitHub 托管机器 | 约 5 分钟；公开后托管机器免费 |
| 建议 | A3 文档写着 MR.ERP DMS 的一处权限漏洞细节 | 先确认已修；没修就和 A1 一起从历史去掉 | 随 A1 |
| 建议 | B1 DMS 测试账号 `dmstest` 口令＝账号名（历史里写过） | 在 DMS 改掉这个口令 | 几分钟，不碰仓库 |
| 可接受 | B2–B6 服务器 IP、邮箱、客户公司名与税号、Actions 记录 | 见下，按需 | 低 |

## 一、密钥与凭据（全部历史：所有分支 + 标签，3411 个提交，23173 个文件版本）

做法：本机 `gitleaks 8.30.1 git --log-opts=--all`；再用自写规则（私钥头、AWS / Google / GitHub / Anthropic / OpenAI / Slack 令牌、
GCP 服务账号 JSON、JWT、带口令的连接串、`*PASSWORD|SECRET|TOKEN|API_KEY*=值`、Fernet 密钥形）逐个扫全部文本文件版本；
按文件名查 `.env`、`site_config`、`*.pem/*.key`、`id_rsa`、`.testenv-secrets`、数据库导出；二进制截图逐张看。全程只在本机，没有上传。

| 结果 | 说明 |
|---|---|
| 私钥、云厂商令牌、GitHub 令牌、Anthropic / OpenAI 密钥、JWT、服务账号 JSON | **0 条** |
| `.env` / `site_config.json` / 证书私钥 / `.testenv-secrets` / 数据库导出 | **从未提交过** |
| gitleaks 27 条 | 全是测试假值（`exp_CONN1_secret`、`pcc_PCD-0123…`、RFC 8291 示例向量）和文件哈希，无效 |
| 自定义规则 194 条口令赋值 | 全是实验栈 / 测试固定值（`*-lab-only`、`regression-lab-only`、`flows-lab-only`、`Tear-Down-2026` 等），只用于本机一次性实验栈 |
| 连接串 3 条 | WeKan 本机实验库 `*-local-only`、测试里的 `user:secret@example.com` |
| LINE 指引截图（控制面「LINE 设置指引」页配图，含已删的 8 张旧图） | Channel secret、访问令牌**都已打码**；只露 Channel ID / LIFF ID（本来就公开） |
| `docs/p4/16-legacy-credentials-found.md` 旧版（1ae3dd40 → 6e31dc90 删去） | 写过「`dmstest` 密码与账号同名」→ B1 |

GitHub Actions：仓库机密只有 `COMPANION_PUBLISH_SSH_KEY`、`PRODUCTION_IMAGE_SSH_KEY`、`PRODUCTION_KNOWN_HOSTS`，
只在手动触发 / 打标签的工作流里用，PR 拿不到。抽查最近一次 stage-production / companion-release / build-production 日志，机密都被遮成 `***`。

## 二、个人与客户信息

| # | 内容 | 位置（首次提交） | 判断 |
|---|---|---|---|
| **A1** | ris（真实客户）一位终端客户：身份证号（校验位正确、府代码与证上老家一致）、生日、称谓、现住址门牌/村/区/府/邮编、手机号、老家地址区县 | `tests/test_dms_customer_update_rules.py`、`apps/pearnly_dms/pearnly_dms/intake/phone_text.py`（注释）、`docs/work/2026-10-06-dms-customer-update-branch-address.md`（4addb5cf，10-06） | **真人敏感信息**，泰国 PDPA 范围；必须从历史去掉 |
| A1b | 身份证号 `11037…460`：DMS 测试账号里的客户 115，多处测试与文档在用 | `scripts/test-dms-*.py`、`tests/test_dms_change_sync.py`、`tests/test_dms_eventlog_reader.py`、`docs/work/2026-09-24-dms-manual-snapshots.md` 等（3bacdbd9 起） | 校验位正确，来源是 MR.ERP 测试库里的录入，**无法确认是否真人**；建议和 A1 一起换成合成号 |
| **A3** | MR.ERP DMS「页面权限拒绝、数据端点未设防」的复现细节（端点、参数） | `docs/work/2026-09-24-dms-manual-snapshots.md` | 这是线上第三方系统（`www.mrerp4sme.com`，经销商在用）的漏洞写法；没修前公开等于披露 |
| B2 | 服务器地址：生产源站 `107.155.65.74`（Cloudflare 后面的真实 IP）、甲骨文两台、办公室公网 IP、退役机器 IP，及 SSH 用户、密钥文件名、端口 | `docs/development/production-capacity.md`、`scripts/claude-ocr/*.sh`、`.github/workflows/stage-production.yml`、多份 `docs/work` | 不是凭据（SSH 只认密钥），但源站 IP 公开后可绕过 Cloudflare 直打；源站是否只放行 Cloudflare **未核实** |
| B3 | 邮箱：用户本人两个 gmail、另一甲骨文账号的 gmail、`pearnlyai@`、`mrerp_erp@`；GCP 项目名与 3 个服务账号名 | `docs/p0`、`docs/p4`、`docs/work`、`scripts/cloud-run/*.sh` | 低；联系电话 `086-889-2228` 本来就印在登录页 |
| B4 | 真实客户与会计客户的公司名 + 税号（如 ris 经销商、冰厂、Express 账套公司）、岳父客户在 polymer 的使用记录 | 大量 `tests/`、`docs/work` | 公司税号是 DBD 公开登记信息；但合起来等于公开客户名单，属商业信息，看用户是否介意 |
| B5 | 身份证样本 40 张、银行流水 3 份、POS 导出 4 份、MR.ERP Excel 模板 | `samples/ocr-id-card`、`tests/fixtures/*` | 均为合成 / 演示数据（「สมชาย ใจดี」假证、虚构「สาธิต」银行、演示店铺），可公开 |
| B6 | 公开后 Actions 记录（2593 次运行）与 24 个产物（安装包、生产镜像 digest）对所有人可见 | GitHub | 日志抽查无机密；介意可公开前删产物 |

另：`docs/work` 共 550 余份运维与排查记录，写满生产现场、客户、成本与供应商细节。上面是按规则扫出来的，
**不保证穷尽**——规则抓不到的散文式描述（真人名字、对话原文）只能人工通读才能确认。

## 三、公开后的安全面（工作流）

| 工作流 | 触发 | runs-on | 机密 | 公开后风险 |
|---|---|---|---|---|
| `development.yml` | `pull_request`（任何人）+ 分支 push | `vars.DEV_RUNS_ON` → **free-arm-2 自有 runner ×3** | 无 | **高**：外人提 PR 即在我们机器上跑任意代码 |
| `companion.yml` | `pull_request`（改 `companion/**`）+ 分支 push | `vars.COMPANION_RUNS_ON` → **办公室测试机** | 无 | **最高**：测试机在办公室局域网，能摸到 accserver、S 盘、Express 账套 |
| `companion-build.yml` | 手动 | 同上 | 无 | 仅协作者可触发 |
| `companion-release.yml` | 标签 `companion-v*` / 手动 | windows-latest、ubuntu | 发布 SSH 密钥 | 仅协作者 |
| `build-production.yml` | 手动 | ubuntu | `GITHUB_TOKEN`（packages: write） | 仅协作者 |
| `stage-production.yml` | 手动 | ubuntu | 生产镜像 SSH 密钥 | 仅协作者 |

- 没有 `pull_request_target`；仓库默认 `GITHUB_TOKEN` 只读，各工作流也都声明了最小权限。外人 PR 拿不到机密，也写不了提交状态（部署闸认的 `pearnly/flows` 伪造不了）。
- 自有 runner 的问题不能靠「只读令牌」或「改触发条件」堵：PR 里可以改工作流文件本身，直接写 `runs-on: [self-hosted, pearnly-win]`。
  个人账号的仓库没有 runner 组可限，GitHub 官方建议公开仓库不用自有 runner。「外部贡献者需审批」只能挡一层，靠人不点错。
- **改法（公开前做）**：删仓库变量 `DEV_RUNS_ON`、`COMPANION_RUNS_ON`（工作流已写好回退到 `ubuntu-24.04` / `windows-latest`），
  在 free-arm-2 和测试机上注销这 4 个 runner；设置里「Fork PR 工作流」选「所有外部贡献者需审批」。公开仓库用托管机器免费，代价只是 Dev 检查从 ARM 回到 x86（之前就是在 x86 上跑的）。
- 第二步加的流程测试工作流：只在本仓分支 push、`main` 部署前和手动时跑，不跟 `pull_request`，回写 `pearnly/flows` 需要 `statuses: write`，外人 PR 拿不到。
- 公开后 GitHub 免费开启机密扫描与推送拦截，算附带好处。

## 四、三条路

| 路 | 做法 | 代价 | 残留风险 |
|---|---|---|---|
| ① 改写本仓历史后公开 | `git filter-repo` 把 A1/A1b/A3 文本换成合成值，强推所有分支与标签 | 10-06 之后约 1770 个提交 SHA 全变：所有窗口的分支 / 工作树要重建；`pearnly/flows` 与 Dev 检查状态全丢，部署前要重跑全套；文档、记忆、部署记录里的 SHA 全部失效；要强推 `main`（违反现行规矩，需用户明确同意，钩子要临时放行）；GitHub 上旧提交按 SHA 仍可能被直接访问，要找 GitHub 支持清理 | 规则没扫到的 `docs/work` 内容仍会公开 |
| ② 新建公开仓库，从清理后的当前代码起一个提交 | 当前树先清掉 A1/A1b/A3（普通提交），新仓库只放这一版；旧私有仓库留作历史档案 | 新仓库没有历史；部署闸沿首父链找全套（`flows_inherit.py`）与旧 SHA 记录要从新仓库重新起算；所有窗口改远端；GHCR 包要给新仓库授权 | 当前树里的 `docs/work` 仍会公开（B2–B4 照旧） |
| ③ 不公开，流程测试用 GitHub 付费机器 | 只加工作流，不动可见性 | 估 10–30 美元/月（4 核 x86 按分钟），是新增固定花费，要用户同意 | 无公开风险 |

**我的建议**：先做 A2（无论哪条路，公开前都要做，也能今天就做）。可见性上倾向 ③；若坚持不花钱，选 ②，且公开前把 `docs/work` 移出公开仓库（或人工通读一遍）——
现有规则扫不全散文里的现场记录，① 改写历史代价最大、又挡不住这部分。B1 改口令与哪条路无关，建议顺手做。

## 待主会话 / 用户定

1. 走 ①、② 还是 ③。
2. A3 漏洞在 MR.ERP DMS 是否已修（用户公司自己的产品）。
3. B4 客户名单与 `docs/work` 是否接受公开。

在收到主会话明确通知前，仓库可见性、runner、仓库变量都没动。

## 第二步：流程用例搬到 GitHub（10-31 下午）

主会话转达：用户自己在 GitHub 设置页改公开，**不清理历史、不新建仓库**（那位客户的资料由用户自行决定）。
主会话已删仓库变量 `DEV_RUNS_ON` / `COMPANION_RUNS_ON`，用 API 删掉 4 个 runner（runner 数 0）。

| 改动 | 内容 |
|---|---|
| `.github/workflows/flows.yml`（新） | 分支 push → `--changed --base origin/main`；`main` push → `--all`；手动可选。`plan` 按 `run.py --plan` 切份并回写 `pending`；每份一台 `ubuntu-24.04` 拉 GHCR 生产镜像（工作流自带令牌，`packages: read`，登录后立刻登出、令牌只走标准输入）、起回归实验室、只建本份站点、`run.py --shard i/n --jobs 3 --migrate`；`report` 汇总核对后回写 `pearnly/flows`。不跟 `pull_request`；分支上新 push 取消旧的，`main` 排队不取消 |
| `scripts/flows/run.py` | 选法抽成 `select()`；加 `--plan`（范围、格数、各份站点）、`--shard I/N`（按（产品, 数据集）排好后连续切）、`--merge`（份数齐全、格与选法一一对上、都是本提交才算过；缺份记不通过）；`--out` 多记范围、提交、份号、用时；重启探活改用本份实有的站点 |
| `development.yml`、`companion.yml`、`companion-build.yml` | `runs-on` 写死 GitHub 机器，去掉仓库变量入口：公开仓库里重新加变量就会再次把自有机器暴露给外人 PR |
| `scripts/production/deploy.sh` | 注释与补跑提示改成先看 GitHub 的 Flows 运行、`gh workflow run flows.yml --ref main -f scope=all` 重跑，本机为后备 |
| `tests/flows/README.md`、`docs/development/verification.md`、`workflow.md` | 写明在哪跑、怎么切份、为什么不跟 PR、不挂自有 runner |

离线核对（本机，不连实验室）：`--plan --all` 得 35 条流程 127 格切 10 份（每份 12–13 格、1–3 个站点）；`--merge` 三份齐全回 `127/127`、缺一份回不通过并写明 `shards 1,3 of 3`、
没有涉及的流程回 `0 flows` 通过；`--shard` 与 `--post-status` 同用被拒。`check-architecture.py` 通过（产物保留按规矩改成 1 天），`actionlint` 只剩存量的提示级条目。

## 暂停：先不公开（10-31 傍晚）

主会话转达用户改主意：**仓库保持私有，以后再改流程**；第二步暂停，审计留作备用。

- 自有 runner 已恢复：主会话加回仓库变量 `DEV_RUNS_ON=pearnly-arm`、`COMPANION_RUNS_ON=pearnly-win`；本窗口用注册令牌（只经标准输入传给远端，不落日志）
  重新注册 free-arm-2 的 `free-arm-2-1/2/3`（`/opt/actions-runner-N`，用户 runner，systemd 服务）和测试机 `test-pc`（`C:\actions-runner`，服务账号 NETWORK SERVICE），只动了 runner 服务。
  回读：4 个 runner 在线，标签 `self-hosted,Linux,ARM64,pearnly-arm` ×3、`self-hosted,Windows,X64,pearnly-win` ×1；仓库仍是 private。
- 空档期间（约 17:10–17:40）有一次 Development checks（运行 41191726347，multi-wh-p2 分支）落在 GitHub 托管机器上（计私有仓库分钟）；
  已对它重跑，重跑由 `free-arm-2-2` 领走，确认跑回自有机器。
- 第二步的改动只在本分支本地提交、**没有推送**：`flows.yml` 在私有仓库会用付费机器，`development.yml` / `companion*.yml` 的 `runs-on` 写死成 GitHub 机器
  只适用于公开仓库。以后真要公开时，先按「三、公开后的安全面」撤 runner，再把本分支变基、本机跑闸门后推送；仍私有时这几处 `runs-on` 改动要先撤回。
