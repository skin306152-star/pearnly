# 流程用例（主会话 10-28 第 3、4 道闸）

产品入口（页面、对话框、扫码、服务端接口）按业务流程在回归实验室里真点一遍：两套固定数据（`scripts/regression/datasets.py`：
`new` 照 polymer 新开户、`old` 照 mrerp 有存量多公司）× 电脑与手机宽度，手机宽度另用假摄像头读条码；断言页面上看到的、库里落的、
页面没有报错、站点 Error Log 没多出前端回传。

| 文件 | 作用 |
| --- | --- |
| `tests/flows/registry.toml` | 登记表：每条流程的模块、起始页、岗位账号、数据集、宽度、`covers`（它覆盖的产品文件） |
| `tests/flows/behind_baseline.txt` | 入口背后的模块（入口在同一 App 内 import 两层）里开拦截时还没有流程的存量清单：改到时只提醒（CI 摘要、部署脚本都列出）；只许减少——补上流程后对应条目必须删掉，不许新加。不在清单里又没有流程的，改到就拦 |
| `tests/flows/not_flows.toml` | 确实没有业务流程可跑的入口文件，逐个写原因（只许按文件登记） |
| `scripts/flows/<流程>.py` | 流程模块：`prepare`（在站点里开单等）、`steps`（页面步骤）、`judge`（页面结果）、`verify`（落库） |
| `scripts/flows/run.py` | 跑法；`--post-status` 把结果回写成 GitHub 提交状态 `pearnly/flows` |
| `scripts/flows/coverage.py` | 覆盖闸（CI 跑）：改到产品入口文件却没有流程登记它 → 失败；`--status` 出现状清单（按产品、按近 30 天改动次数排序），补覆盖的顺序见 [四道闸记录](../../docs/work/2026-10-29-four-machine-gates.md) |
| `scripts/production/deploy.sh` | 部署闸：构建前核对发布标记所指提交的 `pearnly/flows` 是全套通过，不是就停下并打印补跑命令 |

## 本机最多三套实验栈、收尾清残留

回归实验室、其他窗口的流程 / 验收实验栈都在 Mac 上模拟 amd64，很吃 CPU。
本机同一时间最多三套并行；跑完不用的 `docker compose -p <栈名> -f <编排文件> stop`（数据卷保留），收尾 `docker ps -a` 查有没有忘关的容器
（含一次性测试容器）。补跑部署前的全套前，先确认在跑的不超过两套。

## 每个窗口用自己的实验栈

`run.py` 默认连 `pearnly-regression-lab`。别的窗口起自己的一套时用自己的栈名（`-p`）和端口，再用 `PEARNLY_FLOWS_LAB` 告诉 `run.py`，
回写的仍是同一个 `pearnly/flows` 状态：

```bash
export PEARNLY_FLOWS_LAB=pearnly-verify-lab REGRESSION_LAB_PORT=18781 REGRESSION_LAB_PORT_COWORK=18782 REGRESSION_LAB_PORT_DMS=18783
PEARNLY_REGRESSION_SRC=$PWD docker compose -p $PEARNLY_FLOWS_LAB -f infrastructure/compose/regression-lab.yml up -d --wait
docker compose -p $PEARNLY_FLOWS_LAB -f infrastructure/compose/regression-lab.yml exec -T bench env/bin/python /workspace/scripts/regression/lab_init.py
python3 scripts/flows/run.py --changed --base origin/main --post-status
docker compose -p $PEARNLY_FLOWS_LAB -f infrastructure/compose/regression-lab.yml stop
```

`lab_init.py` 建站时做好两件以前要手动补的事（11-01）：
- 测试账号的时区设成 `Asia/Bangkok`，同站点、同生产账号。以前是 Asia/Kolkata，泰国 00:00–01:30 按用户时区取「本月」的页面会落到上个月，库存卡期末对不上。
- 建完站自动跑 `scripts/regression/lab_billing.py`，接上实验室计费。没接时首页报「计费服务暂不可用」，导出按钮不出。

老实验栈重跑一遍 `lab_init.py` 就补齐了，它可以重复跑。
`run.py` 跑前也核对计费：`lab_init.BILLED` 里已建的站点只要有一个没接上，就拦下并打印接计费的命令。

## 两套数据里与公司设定有关的两点（盘点菜单做完整，10-29）

- 新开户报上 Express 账套后要在公司上勾「接 Express」才算接上（连上且勾了才推送 / 同步），`datasets.py` 照正式做法勾上。
- 开户自动建的公司自带一个默认仓（同「新建公司」表单）；第一次 Express 主档同步时它被认作 Express 主仓，所以新开户仍是 7 个仓。

## 在 GitHub 上跑（11-16 起，仓库公开后自动生效）

`.github/workflows/flows.yml`：推分支就在 GitHub 的标准 x86 机器上跑受影响的流程（同 `run.py --changed --base origin/main`，产品地图并集），
按站点切份（`run.py --plan`，每份约 14 格、最多 20 份；一次运行最多同时占 12 台，全套全仓一次只跑一套，给别的检查留机器），每份一台机器起一套回归实验室、只建本份的站点（`scripts/flows/github-lab.sh`）；
汇总（`run.py --merge`）后不通过的格在一台机器上依次补跑一次（`--rerun-failed`），回写同一个 `pearnly/flows`（部署闸、合入钩子、列车照认）。
运行页摘要列出不通过的格、补跑才过的格、留下了数据的格。

| 想做的 | 怎么做 |
| --- | --- |
| 分支受影响的 | 推分支即可；结果看提交状态 `pearnly/flows` 或 Actions 里的 Flows 运行 |
| 发布提交上的全套（部署闸要） | `gh workflow run flows.yml --ref <发布标记或分支> -f scope=all` |
| 只跑几条（不回写状态） | `gh workflow run flows.yml --ref <分支> -f scope=flows -f flows=erp.stocktake.scan,cc.operator` |
| 失败了重跑 | Actions 页面 Re-run failed jobs（只重跑失败的份），或推一个新提交 |

不跑的时候：这个提交已在 `origin/main` 上（main 不自动跑全套）；这个提交已有同范围或全套通过的 `pearnly/flows`（本机跑过再推上来，不覆盖）；
仓库仍私有时（标准机器计费）只有提交说明带 `[flows-on-github]` 的才跑。不跟 `pull_request`，不用自有机器。
生产镜像是私有包，工作流用自带令牌（`packages: read`）拉，拉完登出。本机跑法照旧可用，是 GitHub 出问题时的后备。

## 为什么以前在本机跑

生产镜像只有 linux/amd64；回归实验室在本机 Mac 上跑的就是线上同一个 amd64 镜像（colima 里经 Rosetta 转译；10-31 之前没装 Rosetta，走的是 qemu，慢三十多倍）。放到 free-arm-2 上同样要模拟，
还要给 CI 的 runner 加 root 级的 docker 权限。所以流程套件不在 GitHub 的 runner 上跑，结果由本机回写成提交状态。

## 分支上（只跑受影响的）

```bash
cd <任务工作树>
PEARNLY_REGRESSION_SRC=$PWD docker compose -p pearnly-regression-lab -f infrastructure/compose/regression-lab.yml up -d --wait
python3 scripts/flows/run.py --changed --base origin/main --post-status
```

按改动文件对 `covers` 选流程，另外流程自己的脚本、它引用到的流程辅助脚本（同目录的 `cc_lib`、`erp_lib`、`desk_list`…，也包括引用的别的流程脚本，层层引用都算；`run.py`、`coverage.py` 除外）改了也选上，按 import 自动找，`covers` 里不用写这些脚本；一个都没涉及也回写通过（说明里是 `0 flows`）。改到入口文件却没有登记的，CI 的覆盖闸已经拦下。

分支上只跑受影响的流程，不跑全套；变基没有冲突就不重跑（主会话 10-30 定）。

## 部署前（全套）

部署闸只认全套：说明以 `all flows` 开头、状态 success、就在发布标记所指的那个提交上（或按沿用规则沿用）。

全套每次部署只跑一次，跑在要发布的那个 main 提交上（10-31 起按发布标记部署，不再冻结 main）：
- 选定 main 上要发布的提交，在它上面跑全套、回写；main 照常往前合，不影响这次发布。
- 过了就给它打发布标记并部署：`git tag -a release-<年月日-时分> <提交> -m "production release" && git push origin refs/tags/<标记>`，
  再 `scripts/production/deploy.sh --tag <标记>`；那个提交还是 main 最新时直接 `deploy.sh` 也行（自己打标记）。
- 实验室迁移逐站核 DocType：迁移后少了就停下、不跑流程不回写（`scripts/production/doctype_guard.py`）。

```bash
git -C ~/pearnly fetch origin
git -C ~/pearnly worktree add ~/pearnly-devtools/flows-main origin/main
cd ~/pearnly-devtools/flows-main
(cd frontend && npm ci && npm run build) && (cd frontend/assistant && npm ci && npm run build)   # 前端产物不进 git，新工作树要先建
PEARNLY_REGRESSION_SRC=$PWD docker compose -p pearnly-regression-lab -f infrastructure/compose/regression-lab.yml up -d --wait
docker compose -p pearnly-regression-lab -f infrastructure/compose/regression-lab.yml exec -T bench env/bin/python /workspace/scripts/regression/lab_init.py
python3 scripts/flows/run.py --all --post-status --jobs 3 --out ~/pearnly-devtools/flows-main.json
```

- 回写前 `run.py` 自己核对：实验室挂的产品源码就是这个工作树、产品代码没有未提交改动，否则拒绝回写。
- 不管回不回写，跑前都核对：两个前端单页的产物都在、且比源码新。`frontend/` → `public/pearnly-app/`（DMS 的 LINE 编辑器），`frontend/assistant` → `public/assistant/`（CC 对话页）。缺了或旧了就拦下并打印构建命令。实验室计费也要接上。
- `--post-status` 先重启实验室网页进程（网页用 `--noreload` 起，换了源码不重启就是新旧代码混跑）、把全部站点迁移到挂上来的源码、再重启一次；`--jobs 3` 按站点并行（同一站点的格依次跑）。
- 工作树必须在 `/Users` 下（Docker 挂不了 `/private/tmp`）。跑完 `git worktree remove` 掉。
- 依赖 / Dockerfile / 底座版本变了时，实时源码盖不到：用 `PEARNLY_REGRESSION_LAB_IMAGE=<候选镜像>` 起实验室再跑。

## 个别格补跑（同一提交上合并）

全套里个别格因环境不通过（漏建前端产物、没接计费、偶发超时等，先查共享笔记「已知问题与结论」），环境修好后不用重跑全套：

```bash
python3 scripts/flows/run.py --rerun-failed ~/pearnly-devtools/flows-main.json --out ~/pearnly-devtools/flows-main-merged.json --post-status
```

- 只重跑上次结果里不通过的格，替换后与其余格合并。合并结果标 `rerun`，记 `merged_from` 和 `reran`，然后回写。
- 上次结果必须是同一个提交、产品代码干净时跑的：`--out` 记了 `sha`、`dirty`、`scope`。
  - 换了提交就拒绝，要跑全套。
  - 格与登记表对不上（登记表改过，或上次没跑完）也拒绝。
  - 部分范围（`--flow`、`--dataset`、`--viewport`）的结果不能回写。
- 范围沿用上次的，部署闸照样只认全套：合并全套的说明仍以 `all flows` 开头，末尾写明补跑了哪几格。补跑还不通过就回写失败。
- 合并结果可以再补跑一次，之前补跑过的格照样列出。

## 本机睡眠、实验室没起来

没有状态就没有部署：`deploy.sh` 拦下并打印上面的补跑命令，没有绕过开关。补跑就是按「部署前」一节在本机起实验室跑一遍
（第一次建站加两套数据约 10 分钟，之后每条流程每格约 2 分钟）。跑的是要部署的那个提交；main 在这期间前进了，就对新的提交重跑。

## 状态说明的格式

`<范围>; <流程数> flows; datasets <数据集>; <通过>/<格数> runs passed; <秒>s[; reran <格数>: <流程> <数据集>/<宽度>, …]`。
- 范围是 `all flows` 或 `base <12 位分叉点>`。
- 补跑合并时末尾加 `reran`，秒数是两次之和。
- GitHub 只收 140 字，超出截断加「…」；完整清单在 `--out` 的 `reran` 里。

例如 `all flows; 1 flows; datasets new,old; 4/4 runs passed; 812s`、`all flows; 41 flows; datasets new,old; 149/149 runs passed; 3260s; reran 1: dms.booking.editor new/desktop`。

## 加一条流程

1. 写 `scripts/flows/<流程>.py`（照 `erp_stocktake_scan.py`：`prepare` / `steps` / `judge` / `verify`，`verify` 要查库里落的结果，不能只看能不能打开）。
2. 在 `registry.toml` 登记，`covers` 列出它覆盖的产品文件。
3. `python3 scripts/flows/run.py --flow <id>` 在两套数据、两种宽度上都过，而且要在**新起的实验栈**上单跑也过。

## 流程之间不共用数据（主会话 11-01）

每条流程的数据只能来自两处：`datasets.py` 的固定数据，或者本流程 `prepare` 自己备的。
- 自己备的要能重复跑：有就复用，没有就按正式函数建。
- 不靠排在前面的流程留下的库存、单据、当前公司或界面语言。
- 要「库里已有的各类单据」的（打印、导出），先调 `erp_lib.ensure_documents`：入库、出库、入了库的采购单、销售单、已过账盘点、收银小票、已交班的班次，七类各保证有一张。

以前按登记表顺序跑全套能过，单跑或换了顺序就不过、或者测得少了。起因是 11-01 新栈上单跑库存卡，新开户站点没有库存。

查法：在新起的实验栈上倒过来跑全套，同一站点的格反着跑，靠前面流程留下数据的流程就会排到前面露出来；结果仍按登记表顺序记。

```bash
python3 scripts/flows/run.py --all --order reverse --jobs 3 --out ~/pearnly-devtools/flows-reverse.json
```

改了登记表顺序、加了流程或者改了 `prepare` 的，合入前倒序全套跑一遍。部署闸认的全套不论正序倒序（说明都以 `all flows` 开头）。
