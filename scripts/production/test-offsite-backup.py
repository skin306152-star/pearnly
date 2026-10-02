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
print({"passed": len(checks), "checks": checks})
