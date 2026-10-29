# 流程用例（主会话 10-28 第 3、4 道闸）

产品入口（页面、对话框、扫码、服务端接口）按业务流程在回归实验室里真点一遍：两套固定数据（`scripts/regression/datasets.py`：
`new` 照 polymer 新开户、`old` 照 mrerp 有存量多公司）× 电脑与手机宽度，手机宽度另用假摄像头读条码；断言页面上看到的、库里落的、
页面没有报错、站点 Error Log 没多出前端回传。

| 文件 | 作用 |
| --- | --- |
| `tests/flows/registry.toml` | 登记表：每条流程的模块、起始页、岗位账号、数据集、宽度、`covers`（它覆盖的产品文件） |
| `tests/flows/not_flows.toml` | 确实没有业务流程可跑的入口文件，逐个写原因（只许按文件登记） |
| `scripts/flows/<流程>.py` | 流程模块：`prepare`（在站点里开单等）、`steps`（页面步骤）、`judge`（页面结果）、`verify`（落库） |
| `scripts/flows/run.py` | 跑法；`--post-status` 把结果回写成 GitHub 提交状态 `pearnly/flows` |
| `scripts/flows/coverage.py` | 覆盖闸（CI 跑）：改到产品入口文件却没有流程登记它 → 失败；`--status` 出现状清单（按产品、按近 30 天改动次数排序），补覆盖的顺序见 [四道闸记录](../../docs/work/2026-10-29-four-machine-gates.md) |
| `scripts/production/deploy.sh` | 部署闸：构建前核对要部署的 main 提交 `pearnly/flows` 是全套通过，不是就停下并打印补跑命令 |

## 本机最多两套实验栈、收尾清残留

回归实验室、其他窗口的流程 / 验收实验栈都在 Mac 上模拟 amd64，很吃 CPU。本机同一时间最多两套并行；跑完不用的
`docker compose -p <栈名> -f <编排文件> stop`（数据卷保留），收尾 `docker ps -a` 查有没有忘关的容器（含一次性测试容器）。
补跑部署前的全套前，先确认在跑的不超过一套。

## 为什么在本机跑

生产镜像只有 linux/amd64；本机 Mac 用 Rosetta 跑同一镜像（回归实验室就是这样），free-arm-2 是 ARM，只能 qemu 模拟，
又慢又和线上不完全一样，还要给 CI 加 root 级权限。所以流程套件不在 GitHub 的 runner 上跑，结果由本机回写成提交状态。

## 分支上（只跑受影响的）

```bash
cd <任务工作树>
PEARNLY_REGRESSION_SRC=$PWD docker compose -p pearnly-regression-lab -f infrastructure/compose/regression-lab.yml up -d --wait
python3 scripts/flows/run.py --changed --base origin/main --post-status
```

按改动文件对 `covers` 选流程；一个都没涉及也回写通过（说明里是 `0 flows`）。改到入口文件却没有登记的，CI 的覆盖闸已经拦下。

## 部署前（全套）

部署闸只认全套：说明以 `all flows` 开头、状态 success、就在要部署的那个 main 提交上。

```bash
git -C ~/pearnly fetch origin
git -C ~/pearnly worktree add ~/pearnly-devtools/flows-main origin/main
cd ~/pearnly-devtools/flows-main
PEARNLY_REGRESSION_SRC=$PWD docker compose -p pearnly-regression-lab -f infrastructure/compose/regression-lab.yml up -d --wait
docker compose -p pearnly-regression-lab -f infrastructure/compose/regression-lab.yml exec -T bench env/bin/python /workspace/scripts/regression/lab_init.py
python3 scripts/flows/run.py --all --post-status --out ~/pearnly-devtools/flows-main.json
```

- 回写前 `run.py` 自己核对：实验室挂的产品源码就是这个工作树、产品代码没有未提交改动，否则拒绝回写。
- 工作树必须在 `/Users` 下（Docker 挂不了 `/private/tmp`）。跑完 `git worktree remove` 掉。
- 依赖 / Dockerfile / 底座版本变了时，实时源码盖不到：用 `PEARNLY_REGRESSION_LAB_IMAGE=<候选镜像>` 起实验室再跑。

## 本机睡眠、实验室没起来

没有状态就没有部署：`deploy.sh` 拦下并打印上面的补跑命令，没有绕过开关。补跑就是按「部署前」一节在本机起实验室跑一遍
（第一次建站加两套数据约 10 分钟，之后每条流程每格约 2 分钟）。跑的是要部署的那个提交；main 在这期间前进了，就对新的提交重跑。

## 状态说明的格式

`<范围>; <流程数> flows; datasets <数据集>; <通过>/<格数> runs passed; <秒>s`，范围是 `all flows` 或 `base <12 位分叉点>`。
例如 `all flows; 1 flows; datasets new,old; 4/4 runs passed; 812s`。

## 加一条流程

1. 写 `scripts/flows/<流程>.py`（照 `erp_stocktake_scan.py`：`prepare` / `steps` / `judge` / `verify`，`verify` 要查库里落的结果，不能只看能不能打开）。
2. 在 `registry.toml` 登记，`covers` 列出它覆盖的产品文件。
3. `python3 scripts/flows/run.py --flow <id>` 在两套数据、两种宽度上都过。
