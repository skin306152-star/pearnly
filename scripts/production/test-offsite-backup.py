"""每日异地备份与恢复演练的契约（离线，只读文件内容）。用户 2026-10-02 选方案 B；压测窗口：曼谷 02:00、防重叠、记耗时。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
backup = (ROOT / "scripts/production/offsite-backup.sh").read_text()
drill = (ROOT / "scripts/production/restore-drill.sh").read_text()
upload = (ROOT / "scripts/production/offsite-upload.py").read_text()
timer = (ROOT / "infrastructure/production/pearnly-offsite-backup.timer").read_text()
compose = (ROOT / "infrastructure/production/compose.yml").read_text()
checks = []


def check(name, ok):
    if not ok:
        print("FAIL", name)
        sys.exit(1)
    checks.append(name)


check("上一次还没结束就跳过这一次（flock -n）", "flock -n 9" in backup and '"status":"skipped"' in backup)
check("每次记耗时，超过 45 分钟标 SLOW", "SLOW_SECONDS=2700" in backup and '"slow":%s' in backup)
check("曼谷 02:00（UTC 19:00）触发，避开 00:00 / 03:00 / 04:30", "OnCalendar=*-*-* 19:00:00 UTC" in timer)
check("所有库各一份 --single-transaction（不锁表）", "--single-transaction" in backup and "information_schema.schemata" in backup)
check("测试租户不备文件（可用 PEARNLY_BACKUP_FILE_SITES 覆盖）", "[[ $site == test* ]] && continue" in backup)
check("密钥与站点配置加密后才上传", "openssl enc -aes-256-cbc -pbkdf2" in backup and "secrets.tar.gz.enc" in backup)
check("上传只新建不覆盖、按 md5 回读校验", '"ifGenerationMatch": "0"' in upload and "md5Hash" in upload)
check("周日另存 weekly/", 'weekday == 7' in backup and "weekly/$day" in backup)
check("备份与演练都不 exec 进生产数据库", not re.search(r"docker exec[^\n]*pearnly-production-mariadb", backup + drill))
check("演练在一次性临时库里恢复，不写生产库", "pearnly-restore-drill" in drill and "-h mariadb" in drill
      and not re.search(r"live\(\)[^\n]*(INSERT|UPDATE|DELETE|DROP)", drill, re.I))
check("二进制日志开启并保留 7 天（恢复到任意时刻）", "--log-bin=binlog" in compose and "--binlog-expire-logs-seconds=604800" in compose)
check("runtime.env 不整份 source（有带逗号的值），只取 PEARNLY_BACKUP_* 覆盖项",
      not re.search(r"(^|\s)(\.|source) \S*runtime\.env", backup) and "PEARNLY_BACKUP_*" in backup
      and backup.index("PEARNLY_BACKUP_*") < backup.index("BUCKET=${PEARNLY_BACKUP_BUCKET"))
check("读服务账号密钥（只给 root 读）的上传 / 下载容器以 root 跑",
      all("--user 0:0" in line for line in (backup + drill).splitlines() if "/key.json:ro" in line))
check("校验清单不把自己算进去（先算完再写入）", ") > $work/SHA256SUMS" not in backup and "sha256sum -- *" in backup)

# 每 5 分钟同步改动记录（用户 2026-10-17 定）
ship = (ROOT / "scripts/production/binlog-ship.sh").read_text()
ship_timer = (ROOT / "infrastructure/production/pearnly-binlog-ship.timer").read_text()
install = (ROOT / "scripts/production/install-offsite-backup.sh").read_text()
check("每日备份记下改动记录位置（--master-data=2），恢复时从那里接着回放", "--single-transaction --master-data=2" in backup)
check("改动记录每 5 分钟同步，上一次没完就跳过", "OnCalendar=*:2/5" in ship_timer and "flock -n 9" in ship)
check("先切出新文件，只传已经关上的（正在写的那个不传）", "FLUSH BINARY LOGS" in ship and '${files[@]:0:${#files[@]}-1}' in ship)
check("改动记录不 exec 进生产数据库，读密钥的上传容器以 root 跑",
      not re.search(r"docker exec[^\n]*mariadb", ship)
      and all("--user 0:0" in line for line in ship.splitlines() if "/key.json:ro" in line))
check("改动记录按时间命名、按 UTC 日期分目录，上传沿用只新建的 offsite-upload.py",
      '"binlog/$day"' in ship and "%Y%m%dT%H%M%SZ" in ship and "offsite-upload.py" in ship)
check("安装脚本同时装两个定时器", "pearnly-binlog-ship.timer" in install and "pearnly-offsite-backup.timer" in install)

# 甲骨文那台的守望：网站连续 3 分钟打不开才发信、恢复发一封；备份按时判断
import datetime as dt
import importlib.util
spec = importlib.util.spec_from_file_location("watch", ROOT / "infrastructure/oracle-watch/watch.py")
watch = importlib.util.module_from_spec(spec); spec.loader.exec_module(watch)
state, events = None, []
for ok in (False, False, False, False, True, True):
    state, event = watch.step(state, ok, watch.SITE_FAILURES, "t", "HTTP 502")
    events.append(event)
check("网站连续 3 分钟打不开发一封告警，期间不重复，恢复发一封", events == [None, None, "down", None, "up", None])
state, event = watch.step(None, False, 1, "t"); state, again = watch.step(state, False, 1, "t")
check("备份检查一次不对就告警、不重复", (event, again) == ("down", None))
def _bucket_round(state, readable):
    def gcs():
        if not readable:
            raise TimeoutError("ReadTimeout")
        return object(), None
    watch._gcs, watch.gcs_list, watch.watch_env = gcs, lambda session, prefix: ["x"], lambda: {}
    return [subject for subject, _ in watch.check_backups(state, dt.datetime(2026, 10, 21, 13, 40, tzinfo=dt.timezone.utc), "t")]
state, rounds = {}, []
for readable in (False, False, False, False, True, False):
    rounds.append(_bucket_round(state, readable))
check("备份桶连续 3 次读不了才告警、不重复，读成功发一封已恢复并清掉告警，下次再读不了重新计数",
      rounds == [[], [], ["Pearnly 告警：备份存储桶读不了"], [], ["Pearnly 已恢复：备份存储桶"], []]
      and state["备份存储桶"]["failures"] == 1 and not state["备份存储桶"]["alerted"])
bkk = watch.BANGKOK
check("凌晨 3 点前看前一天的每日备份，3 点后看当天",
      watch.daily_due_day(dt.datetime(2026, 10, 17, 2, 59, tzinfo=bkk)) == dt.date(2026, 10, 16)
      and watch.daily_due_day(dt.datetime(2026, 10, 17, 3, 0, tzinfo=bkk)) == dt.date(2026, 10, 17))
now = dt.datetime(2026, 10, 17, 8, 30, tzinfo=dt.timezone.utc)
names = ["binlog/2026-10-17/20261017T081000Z-binlog.000120.gz", "binlog/2026-10-17/20261017T081500Z-binlog.000121.gz", "binlog/x/README"]
check("改动记录按最新一份离现在多少分钟判断，认不出的名字跳过",
      watch.binlog_minutes_behind(names, now) == 15 and watch.binlog_minutes_behind([], now) is None)
drill_oracle = (ROOT / "infrastructure/oracle-watch/drill.sh").read_text()
check("甲骨文演练：导入后从导出文件记的位置按库回放，改动记录编号必须连续，成功失败都发信",
      "--database=$db --start-position=$pos" in drill_oracle and "改动记录断档" in drill_oracle
      and 'mail "Pearnly 恢复演练：$status"' in drill_oracle)
print({"passed": len(checks), "checks": checks})
