#!/usr/bin/env bash
# 每天异地备份（用户 2026-10-02 选方案 B，见 docs/work/2026-10-02-offsite-backup.md）。宿主机 root 运行，systemd 定时器曼谷 02:00
# 触发（pearnly-offsite-backup.timer；避开 Frappe 每日任务 00:00 前后、DMS 03:00 全量回读与 04:30 补读）。
#
# 内容：所有数据库（真实 + 测试租户、billing、keycloak、mysql 账号表）各一份 mariadb-dump --single-transaction（不锁表；一次性
# 客户端容器连数据库网络，不 exec 进 mariadb）；真实租户的私有 / 公开文件（站点名以 test 开头的测试租户不备文件，可用
# PEARNLY_BACKUP_FILE_SITES 覆盖）；runtime.env、各站点 site_config（含加密钥）、/opt/pearnly/secrets 下的密钥文件打包后用
# /opt/pearnly/secrets/backup.key 加密。上传到 gs://pearnly-backups-sg 的 daily/<曼谷日期>/，周日另存 weekly/<日期>/；
# 删除只靠存储桶生命周期（daily 14 天、weekly 56 天），上传身份只能新建对象（不能读、删、覆盖）。
#
# 上一次还没结束就跳过这一次（flock）；每次的起止、耗时、大小记进 /opt/pearnly/state/backup.log（超过 45 分钟标 SLOW），
# 最近一次写 backup-last.json。成功后删掉本机暂存，失败时留着查。
set -uo pipefail
STATE=/opt/pearnly/state; LOG=$STATE/backup.log; SECRETS=/opt/pearnly/secrets; DEPLOY=/opt/pearnly/deploy
# runtime.env 不是 shell 脚本（有带逗号、空格的值），只取备份自己的覆盖项，不整份 source
if [[ -f $SECRETS/runtime.env ]]; then
  while IFS='=' read -r key value; do [[ $key == PEARNLY_BACKUP_* ]] && export "$key=$value"; done < $SECRETS/runtime.env
fi
BUCKET=${PEARNLY_BACKUP_BUCKET:-pearnly-backups-sg}; SLOW_SECONDS=2700
mkdir -p $STATE
exec 9>$STATE/backup.lock
if ! flock -n 9; then
  printf '{"at":"%s","status":"skipped","reason":"previous backup still running"}\n' "$(date -u +%FT%TZ)" >> $LOG; exit 0
fi
started=$(date +%s); day=$(TZ=Asia/Bangkok date +%F); weekday=$(TZ=Asia/Bangkok date +%u)
work=/opt/pearnly/backups/offsite/$day; rm -rf /opt/pearnly/backups/offsite; mkdir -p $work; chmod -R 700 /opt/pearnly/backups/offsite
fail() { local took=$(( $(date +%s) - started ))
  printf '{"at":"%s","day":"%s","status":"failed","step":"%s","seconds":%d}\n' "$(date -u +%FT%TZ)" "$day" "$1" "$took" | tee -a $LOG > $STATE/backup-last.json
  exit 1; }

M=pearnly-production-mariadb-1
NET=$(docker inspect $M --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
DBIMG=$(docker inspect $M --format '{{.Config.Image}}')
PW=$(docker inspect $M --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^MARIADB_ROOT_PASSWORD=//p')
client() { docker run --rm -i --network "$NET" --memory 512m -e MYSQL_PWD="$PW" --entrypoint "$1" "$DBIMG" -h mariadb -uroot "${@:2}"; }

# 1) 数据库：逐库导出、压缩
dbs=$(client mariadb -N -B -e "SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('information_schema','performance_schema','sys')") || fail list_databases
for db in $dbs; do
  # --master-data=2：在导出文件头注释里记下这一刻的改动记录位置，恢复时从这里接着回放（binlog-ship.sh 每 5 分钟传的那些）；
  # MariaDB 配 --single-transaction 时用一致性快照取位置，不加全局读锁。
  client mariadb-dump --single-transaction --master-data=2 --quick --routines --events --triggers --hex-blob --databases "$db" | gzip -6 > $work/db-$db.sql.gz
  [[ ${PIPESTATUS[0]} == 0 && ${PIPESTATUS[1]} == 0 ]] || fail "dump $db"
done

# 2) 真实租户的文件（测试租户不备文件；PEARNLY_BACKUP_FILE_SITES 可指定站点名，空格分隔）
for volume in $(docker volume ls -q | grep '^pearnly-production_sites-'); do
  root=$(docker volume inspect -f '{{.Mountpoint}}' $volume)
  for config in $root/*/site_config.json; do
    site=$(basename "$(dirname "$config")")
    if [[ -n ${PEARNLY_BACKUP_FILE_SITES:-} ]]; then [[ " $PEARNLY_BACKUP_FILE_SITES " == *" $site "* ]] || continue
    else [[ $site == test* ]] && continue; fi
    dirs=(); for d in private/files public/files; do [[ -d $root/$site/$d ]] && dirs+=("$d"); done
    (( ${#dirs[@]} )) || continue
    tar -czf $work/files-$site.tar.gz -C $root/$site --exclude=private/backups "${dirs[@]}"; rc=$?
    (( rc <= 1 )) || fail "files $site"   # 1 = 打包时有文件在变，照样可用
  done
done

# 3) 密钥与站点配置（加密）
[[ -s $SECRETS/backup.key ]] || fail missing_backup_key
bundle=$(mktemp -d); mkdir -p $bundle/sites
cp $SECRETS/runtime.env $bundle/ 2>/dev/null; cp $SECRETS/*.json $bundle/ 2>/dev/null; rm -f $bundle/backup-writer.json $bundle/backup-reader.json
for volume in $(docker volume ls -q | grep '^pearnly-production_sites-'); do
  root=$(docker volume inspect -f '{{.Mountpoint}}' $volume); product=${volume#pearnly-production_sites-}
  mkdir -p $bundle/sites/$product; cp $root/common_site_config.json $bundle/sites/$product/ 2>/dev/null
  for config in $root/*/site_config.json; do mkdir -p $bundle/sites/$product/$(basename "$(dirname "$config")"); cp "$config" $bundle/sites/$product/$(basename "$(dirname "$config")")/; done
done
tar -czf - -C $bundle . | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass file:$SECRETS/backup.key > $work/secrets.tar.gz.enc \
  || { rm -rf $bundle; fail encrypt_secrets; }
rm -rf $bundle

# 4) 清单
revision=$(docker inspect pearnly-production-cowork-web-1 --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' 2>/dev/null)
# 先算完再写：重定向会先建出空的 SHA256SUMS，若直接 `sha256sum * > SHA256SUMS`，清单会把自己（空文件）也算进去
sums=$(cd $work && sha256sum -- *) || fail checksums; printf '%s\n' "$sums" > $work/SHA256SUMS
printf '{"day":"%s","image_revision":"%s","databases":"%s"}\n' "$day" "$revision" "$(echo $dbs)" > $work/manifest.json

# 5) 上传（生产镜像里的 Python + google-auth；只新建、不覆盖；以 root 跑，密钥文件只给 root 读）
prefixes="daily/$day"; [[ $weekday == 7 ]] && prefixes="$prefixes weekly/$day"
APPIMG=$(docker inspect pearnly-production-cowork-web-1 --format '{{.Config.Image}}')
docker run --rm --user 0:0 --memory 512m -v $work:/backup:ro -v $SECRETS/backup-writer.json:/key.json:ro \
  -v $DEPLOY/scripts/production/offsite-upload.py:/upload.py:ro --entrypoint /home/frappe/frappe-bench/env/bin/python \
  "$APPIMG" /upload.py --key /key.json --bucket $BUCKET --source /backup $prefixes || fail upload

took=$(( $(date +%s) - started )); bytes=$(du -sb $work | cut -f1); slow=false; (( took > SLOW_SECONDS )) && slow=true
printf '{"at":"%s","day":"%s","status":"ok","seconds":%d,"bytes":%s,"slow":%s,"prefixes":"%s"}\n' \
  "$(date -u +%FT%TZ)" "$day" "$took" "$bytes" "$slow" "$prefixes" | tee -a $LOG > $STATE/backup-last.json
rm -rf /opt/pearnly/backups/offsite
