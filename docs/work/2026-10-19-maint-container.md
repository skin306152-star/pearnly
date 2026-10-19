# 2026-10-19 生产维护容器不自动回收：激活时的剩余缺口

分支 `pearnly/maint-container`，起点 `pearnly/batch-1019b` 60184389。不连服务器，只改部署工具和离线测试。

## 已有的（未改）

- `f2438353`：改已有站点、带 `restart` 的工单做完就收维护容器；`activate-product.sh` 在容量等待前按名字 `docker rm -f pearnly-maintenance-<产品>`（不看标签、不看占用）。
- `ae7cd867`：`publish_site` 之后、站点已对外后的每一步（单独的 `set_ocr` / `set_assistant` / 改 LINE 等）做完就收；开通中途的步骤仍共用一个容器。
- 两条都已随 4090d01f、5fefe6cd 上生产。另有 `7b806173`：激活先跑 `ensure-swap.sh`，再 `capacity.py --wait-seconds 600`。

## 这次补的

`scripts/production/activate-product.sh` 激活某产品组时，收维护容器前先核对，顺序为：持 `provision.lock` → 收本组残留维护容器 → `ensure-swap.sh` → 容量检查（等 600 秒）→ 其余照旧。

- 只看 `pearnly-maintenance-<本产品>` 这一个名字；别组的维护容器、compose 容器一概不查不动。
- 标签必须是 `pearnly.role=maintenance` 且 `pearnly.group=<本产品>`（`provision-agent.py` `ensure_maintenance` 建时打的，2026-09-20 起就有）；名字对、标签不对的留着并告警。
- 占用按开户代理现有机制：开户代理整张工单持 `provision.lock`，激活脚本在上面已 flock 同一把锁，此刻没有工单在跑。另加一道：容器里还有 `site-step.py` 进程（上一张工单的 SSH 断了、步骤还在跑完）也算在用，留着并告警，不中途杀掉。`docker top` 的输出先存下再匹配，避免 `pipefail` 下 `grep -q` 的 SIGPIPE 误判成「没在用」。
- 收不掉（`docker rm` 失败）只告警，不挡激活，容量检查照做。

## 测试

`scripts/production/test-activate-product.py`：容量检查和 ensure-swap 的替身改成记录调用，能核顺序；新增 / 改写四个用例：

- 有残留（dms / erp / cowork）→ 持锁后先收，再 ensure-swap，再 `capacity --product <产品> --wait-seconds 600`，再进维护模式；只收这一个；
- 别组残留 → 不查不收；
- 正在跑 site-step、标签是别组、没有维护标签 → 都不收，告警，容量检查照做；
- 没有残留 → 不调 `rm` / `top`，照常；`docker rm` 失败也不挡激活。

用 HEAD 上的旧脚本反跑，新用例 5 处失败，说明能拦住旧行为。`test-site-step-capacity.py`、`check-production-capacity.py`、`test-provision-agent.py` 照过。

## 部署与回读

- 只改部署工具，不改镜像内业务代码；下次生产部署在「同步部署工具」那一步（构建 → stage → 同步部署工具 → 各产品激活 → 回读）随之到 `/opt/pearnly/deploy/scripts/production/activate-product.sh`（`check-deploy-image.py` 会核对服务器副本与镜像副本一致）。不需要单独重发容器。
- 部署后回读（只读）：
  - 激活输出里没有 `is not a provisioning maintenance container` / `still running a provisioning step` / `could not be removed` 告警；有就按告警查那个容器。
  - 三个产品都激活完：`docker ps -a --filter label=pearnly.role=maintenance --format '{{.Names}} {{.Label "pearnly.group"}} {{.Status}}'` 应为空（激活后、下一张开户工单前不该有维护容器）。
  - 服务器上的脚本与本分支一致：`grep -c 'maintenance/$product' /opt/pearnly/deploy/scripts/production/activate-product.sh` 为 1。
