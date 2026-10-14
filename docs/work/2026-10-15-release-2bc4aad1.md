# 2026-10-15 生产发布 `2bc4aad1`（夜间批：教程问题 1–12、科目建议、公司主档、ccs）

改动与证据见 [夜间批记录](2026-10-15-cc-night-batch.md) 与 [公司主档记录](2026-10-15-cc-company-master.md)。用户 2026-10-15 凌晨授权自主推进（「今晚自主推进」「希望醒来能验收新成品」），按「攒批、进维护、立即部署」口径执行。

## 发布前

- main 快进 `6775c0ff → 2bc4aad1`，远端回读一致；分支 CI（Development checks）在 `2bc4aad1` 成功，Companion checks（Windows）在 `ef2cd70a` 成功（之后没有 CC 桌面版改动）。
- 本地全闸门 78 步全绿；生产镜像候选离线检查（`9f0293af`，与 `2bc4aad1` 只差文档）71/71、退出码 0。
- 测试机实测（lab，实时源码）：第 2、3、4、9、10、11 条，公司主档添加 / 对话 / 知识库 / 允许卡片 / 待处理 / 查询范围，见夜间批记录。

## 发布

- 构建 `build-production.yml` run 38645159350 → `sha256:232bf6ea277660ad7eb7b5666d10e93125e202dc286fecb30c73564cbe91a37f`（revision `2bc4aad1`）；`stage-production.yml` run 38646045981 成功。
- 生产激活（日志 `/opt/pearnly/state/deploy-2bc4aad1.log`，曼谷 05:13 开始）：部署工具与镜像一致；ERP、Cowork、DMS 各自进维护激活成功；基础层激活成功，05:19 结束。

## 回读（只读，05:2x）

- 全部生产容器都在新镜像；三个 ocr-worker 都带 `FRAPPE_BACKGROUND_WORKERS_NOFORK=1`。
- 六站点（mrerp、testerp、erpcowork、testcowork、ris、testdms）`maintenance_mode=0`，部署以来 Error Log 为 0。
- Cowork 两站点：公司主档三张表都在、会话表五个新字段都在、补丁 `v0_0_18_history_push_targets` 已记；ERP、DMS 四站点会话表新字段都在（共用层 DocType）。
- mariadb 内存 3.6 / 8 GiB；mrerp、erpcowork、ris 的 `/api/method/ping` 200；fatcc.com 首页 200（其 `/api/method/ping`、`/login` 回 403，入口只放行指定路径）。
- testcowork：`lab-stop cowork` 切回生产（公共 DNS 解析 107.155.65.74，ping 200），生产 testcowork 定时任务已恢复（`enable_scheduler=1`、未暂停）。测试机 lab 里建的公司主档与知识库只在 lab 数据里，生产 testcowork 没有。
- 服务器上另有一个 `pearnly-maintenance-cowork` 容器（已运行 2 天，非本次产生），未处理，登记待核。

## CC 桌面版 1.6.0

- `companion-release.yml` target=prod run 38647761272 成功；`https://pearnly.com/downloads/companion/latest.json` 回读 `version 1.6.0`、`source_sha 2bc4aad1`、sha256 `a2ff49e7…539b`、安装包 152,756,975 字节、HTTP 200。
- 客户端在下次心跳拿到新版自动更新（开着智能应用控制的电脑会被拦，1.6.0 起提示「下载新版」）。

## 回退

- 服务端回到 `6775c0ff` 镜像即可：新表、新字段旧代码不读；补丁只是补票上的推送目标，不需要回滚。
- 例外：电脑用 1.6.0 加到 21 个以上根目录后，不能回退到 20 上限的服务端（规格 §15）。

## 待用户验收（真机）

见夜间批记录「未做 / 下一批」与交付说明：在电脑上为公司选文件夹（1.6.0）、iPhone 工作状态行与断网重连、Safari 中文输入法回车、多设备侧栏同步、早上 08:30 待处理汇总推送。
