# 2026-09-30 夜 — 10-01 晨 生产部署与压测配合记录（本窗口）

用户：「随时可以部署上线，和另一个窗口沟通好，最好一步上线」，之后每一批「部署」均由用户在本窗口确认。全部按 ERP → Cowork → DMS
用 `activate-product.sh` 逐个激活（各自维护、备份、迁移），部署工具目录按镜像同步、`check-deploy-image` 一致。时间为 UTC。

| main | 镜像 | 激活 | 内容 |
|---|---|---|---|
| dd6a775e | 8b8a1ff4 | 16:04–16:12 | 三对账收尾（reread / 作废一致 / 行自检 / 进行中页 / 首页订阅页 / 等候页按页进度 / 预览铺满）、三区域 Document AI、进程数可配、ERP 分类记忆、LINE 免密等 |
| 1e53c2a8 | 7940d10d | 20:35–20:42 | 识别容器不 fork（NOFORK）、提交繁忙自动排队重交、识别结论推送（轮询 5 秒兜底） |
| ba764775 | 791b2561 | 21:39–21:46 | 上传前浏览器缩照片（站点开关）、工作台总数在数据库里数 |
| 61771972 | f578381a | 22:56–23:04 | 不 fork 识别进程按内存高水位温和重启（after_job，默认 300MB） |
| 35f418a8 | e85edf7d | 01:41–01:49 | 检查点按行存、受理去锁（压测窗口）；录入分类树不再锁工作空间、对账按站点限流（默认 6） |

- 每次回读：六个站点维护关、部署后 Error Log 0（DMS 进维护那几秒的定时任务提示除外）、ping 200；三个 ocr-worker NOFORK=1。
- 站点开关：`pearnly_shrink_photos=1` 打开在 erpcowork / testcowork / mrerp / testerp（用户 2026-10-01 同意）；DMS 没有该上传口。
- 未完成并已移交：erpcowork 的 LINE set_line 重排（控制面缺 webhook_url，「LINE 绑定无反应」修复并负责）。

## 压测配合（testcowork）

- 压测账号：stress-test@pearnly.local 2026-09-30 16:15:21 建；stress-01…50@pearnly.local 21:49:08 建（角色：Desk User、录入、三对账、个人信息，无管理员；
  密钥只在生产 `/root/pearnly-stress/`，root 600）。2026-10-01 02:51:22 全部 51 个删除并回读（剩 0、带 API key 的 0），密钥目录已删。
- 慢查询日志：按用户同意在压测期间开（0.05 秒、TABLE），2026-10-01 02:50:22 关闭（long_query_time 回 10）。
- 事故：2026-10-01 00:49:13 本窗口 `docker exec` 进 mariadb 容器查 `mysql.slow_log`，exec 进程计入数据库 3g 额度，mariadbd 被 OOM 杀并自动重启
  （crash recovery，slow_log 表被标 crashed，已 REPAIR）；其后又一次 exec 用 `-B` 整块读回 slow_log，客户端 RSS 约 2GB，被「LINE 绑定无反应」发现后结束。
  规矩：生产查库不 exec 进 mariadb 容器，改用一次性客户端容器走网络连 `mariadb:3306`，大结果只取聚合。22:36 那次 OOM 是 2g 上限本身不够（数据库现 4g）。
- 结论：页面段数据库不是瓶颈（网页进程 CPU 跑满）；识别段头号锁等待是录入分类树每次存结果锁工作空间（已修，复压后消失）；复压后新热点是计费按租户锁
  `tabPearnly Tenant`（压测窗口接手）；pB 读取差异大概率是高峰时 Document AI 落到美国区 v2.1（新加坡、孟买每分钟各 10 次），处理方式待用户在提额后定。
- 工作台组合索引 (workspace, staged, modified)：测试机 EXPLAIN 显示不会被使用（表小、权限条件改写为连接），未上线。
