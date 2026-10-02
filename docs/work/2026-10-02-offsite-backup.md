# 每日异地备份与恢复到任意时刻（2026-10-02）

用户选方案 B（经压测窗口转达）：每天备份所有租户的数据库（真实 + 测试，不含测试语料文件），连同真实租户的私有文件、站点配置、加密钥、
加密后的 runtime.env，传到新加坡 `pearnly-backups-sg`；每天的留 14 天、每周日的留 8 周，靠生命周期自动删除；只写服务账号上传；
宿主机本地定时器、曼谷 02:00，不开任何云上定时或常驻资源。同时开二进制日志保留 7 天（只占本机盘、零费用），随下一次 foundation 重建生效。

## 现状（只读核实，2026-10-02）

- 只有部署时的 `bench backup --with-files`（activate-product / activate-foundation），每站留最近 3 次，存在本机 sites 卷；没有每日备份、没有异地副本。
- `log_bin = 0`，不能恢复到任意时刻；没有恢复演练记录。
- 数据量：数据库合计约 1.56GB（testcowork 约 1.04GB），压缩约 10%；真实租户私有文件约 2MB，testcowork 测试语料 4.4GB（不备）。

## GCP 资源（本窗口建，2026-10-02）

- 存储桶 `gs://pearnly-backups-sg`：asia-southeast1、STANDARD、统一权限、强制禁止公开访问、软删除关（不另计费）。
  生命周期：`daily/` 14 天删、`weekly/` 56 天删。
- `pearnly-backup-writer@pearnly.iam.gserviceaccount.com`：只在该桶有 `roles/storage.objectCreator`（只新建，不能读、删、覆盖）。
- `pearnly-backup-reader@pearnly.iam.gserviceaccount.com`：只在该桶有 `roles/storage.objectViewer`（恢复演练用）。
- 两个账号的密钥在上线时生成，直接写到服务器 `/opt/pearnly/secrets/backup-{writer,reader}.json`（600），不落本机、不进仓库。
- 预计费用：每天约 160MB × 22 份 ≈ 3.5GB，标准存储约 $0.08 / 月；上传免费；演练每次下载约 $0.02–0.03。

## 实现

- `scripts/production/offsite-backup.sh`（宿主机 root）：flock 防重叠（上次没完就跳过并记一条）；一次性客户端容器逐库
  `mariadb-dump --single-transaction`（不 exec 进数据库）；站点名以 `test` 开头的不备文件（`PEARNLY_BACKUP_FILE_SITES` 可覆盖）；
  runtime.env、`/opt/pearnly/secrets/*.json`（不含备份账号自己的密钥）、各站点 site_config 与 common_site_config 打包后用
  `/opt/pearnly/secrets/backup.key` 加密（AES-256、PBKDF2）；SHA256SUMS 与 manifest；在生产镜像的一次性容器里用
  `offsite-upload.py` 上传（`ifGenerationMatch=0` 只新建，按 md5 回读校验）到 `daily/<曼谷日期>/`，周日另存 `weekly/`；
  每次的起止、耗时、大小写进 `/opt/pearnly/state/backup.log`，超过 45 分钟标 `slow`，最近一次写 `backup-last.json`。
- `infrastructure/production/pearnly-offsite-backup.{service,timer}`：UTC 19:00（曼谷 02:00），避开 Frappe 每日任务 00:00 前后、
  DMS 03:00 全量回读与 04:30 补读；Nice 10、IO idle。`scripts/production/install-offsite-backup.sh` 装定时器，首次生成 `backup.key`。
- `restore-drill.sh`：①只读账号下载最新一份、核 SHA256 ②一次性临时 mariadb（2G、不开端口）逐库导入 ③表数与关键表行数「备份 vs
  生产现在」对比、文件包 tar -t、密钥包解密 tar -t ④在恢复出的库里抽读一张票、一份银行对账及其行 ⑤记耗时，删临时容器、卷和文件。
- compose：MariaDB `--log-bin=binlog --binlog-expire-logs-seconds=604800 --sync-binlog=0`（随下一次 foundation 重建生效）。
- `scripts/production/test-offsite-backup.py`（进 Development checks）守住以上约定；容量检查加「二进制日志 7 天」。

## 加密钥匙（必须另存）

`/opt/pearnly/secrets/backup.key` 在服务器上生成（install 时），**请用户自己从服务器取出存进自己的密码管理器**；本窗口和压测窗口都不传、
聊天里不出现钥匙内容。服务器整机丢失时没有这把钥匙，密钥包（站点加密钥、runtime.env）解不开——数据库和文件照样能恢复，但要重建这些配置。

## 上线步骤（随下一批）

1. 镜像带上本分支，激活后部署工具里有新脚本与 unit 文件；foundation 重建时 MariaDB 开二进制日志。
2. 生成两个服务账号密钥直接写到服务器 secrets（600）；`install-offsite-backup.sh` 装定时器、生成 `backup.key` 并提示用户取走。
3. 手动跑一次 `offsite-backup.sh` 验证上传；马上做第一次恢复演练（`restore-drill.sh`），结果写进本记录。
