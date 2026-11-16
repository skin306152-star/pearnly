# 流程用例与候选检查搬到 GitHub 免费机器（2026-11-16）

用户 11-16 定：仓库改公开（用户自己在设置里点），公开仓库的 GitHub 标准机器免费、同时最多 20 台；流程用例和候选检查搬上去并行跑，本机实验栈不再是瓶颈。
取代 11-01「流程测试留本机」的决定。分支 `pearnly/flows-on-github-free`，基线 origin/main 4879255e9。
10-31 草稿分支 `pearnly/flows-on-github-ed0bb3` 只取思路；它把 development / companion 的 `runs-on` 写死成 GitHub 机器的改法没照搬，仍用仓库变量切换。

## 改动

| 文件 | 内容 |
|---|---|
| `.github/workflows/flows.yml`（新） | plan → 分份矩阵 → finish。分支 push：`--changed --base origin/main`（产品地图并集）；手动：changed / all / flows（指定流程不回写）。每份：两个前端构建 → 拉生产镜像（自带令牌 `packages: read`）→ 起回归实验室、只建本份站点 → `run.py --shard I/N --jobs 3`。finish：`--merge` 汇总；不通过的格 ≤30 时在一台机器上 `--rerun-failed --jobs 1` 补跑一次；回写 `pearnly/flows`（同名同格式）；摘要列不通过、补跑才过、留下数据的格 |
| 不跑的情况 | 仓库私有时 push 不跑（提交说明带 `[flows-on-github]` 例外，校准用）；提交已在 origin/main；提交已有同范围或全套通过的 `pearnly/flows`（不覆盖本机跑过的全套，部署闸才认） |
| `.github/workflows/candidate-offline.yml`（新） | 线上生产镜像里跑 `ci-candidate-checks.sh offline`（手动可选 parallel），挂本提交五个 App 源码 |
| `scripts/ci-candidate-checks.sh` | `CANDIDATE_APPS=1` 时给 offline_py 挂五个 App 源码；build-production 不设，行为不变 |
| `scripts/flows/run.py` | `--plan`（按站点切份：小站点装一份、大站点按格数分份，每份 ≤1 个大站点）、`--shard I/N`、`--merge`（缺份的格记不通过，补跑接得上）；`--out` 记份号；重启探活用本次建了的站点 |
| `scripts/flows/github.py`、`github-lab.sh`（新） | 工作流的定范围 / 跳过判断 / 补跑站点 / 摘要；起实验室（登录拉镜像立刻登出） |
| `tests/test_flows_github.py`（新，Development checks 跑） | 切份覆盖与确定性、汇总缺份、跳过规则、补跑站点、工作流不跟 PR 不用自有机器 |
| 文档 | `tests/flows/README.md`、`verification.md`、`workflow.md`、`deploy.sh` 补跑提示先给 GitHub 跑法 |

全套现为 134 条流程 414 格：切 20 份（大站点每份 18–24 格，DMS 两站 6 格一份）。

## 公开前核对

见共享笔记「公开前核对-GitHub免费机器-11-16.md」：密钥 0 真命中；必须同步做的是 fork PR 工作流设为全部外部贡献者需审批、切回 GitHub 机器后注销自有 runner。

## 校准（11-16 公开后，运行 43850195036，e4bec15e9）

| 项 | 数 |
|---|---|
| 每份准备（拉镜像 + 起栈 + 建站） | 108–160 秒 |
| 每份跑格（18–24 格，`--jobs 3`，多为单站点依次跑） | 约 8–15 分钟；整份 14–22 分钟 |
| 全套墙钟（含账号 20 台上限下的排队与补跑约 12 分钟） | 约 52 分钟 |
| 结果 | 409/414；补跑才过 3 格（dms.booking.editor 桌面两格为 Chrome 调试端口冲突，flows-concurrency 修；erp.stocktake.new 新开户手机格靠前格数据） |
| 换到 Linux 才暴露的 | cowork.recon.run 步骤 JSON 超过 Linux 单参数 128 KB 上限（b60877c86 改走 @文件） |
| 待查 | erp.cashier.camera 新开户手机格开班没成功（交流程工具窗口） |
| 候选检查（offline） | 约 5 分钟 |

手动全套与分支 push 原来同一并发组会互相取消，aa08cb2e9 改为按事件与范围分组。

## 待办

- 私有分钟数校准一次（待主会话批）；公开后跑一次全套校准墙钟。
- 公开后主会话通知再删 `DEV_RUNS_ON` / `COMPANION_RUNS_ON`（本窗口不动）。
