#!/usr/bin/env bash
# 每 5 分钟把数据库改动记录（二进制日志）传到异地（用户 2026-10-17 定）：主服务器整台丢了，最多丢 5 分钟的数据
# （原来只有每天一份备份，最多丢一天）。宿主机 root 运行，systemd 定时器 pearnly-binlog-ship.timer 触发。
#
# 做法：一次性客户端容器执行 FLUSH BINARY LOGS（切出一个新文件，不 exec 进 mariadb），把已经关上、还没传过的日志文件
# gzip 后传到 gs://pearnly-backups-sg/binlog/<UTC 日期>/<文件最后修改时刻>-<文件名>.gz（名字按时间排序，库重建后编号重来也不撞名）；
# 正在写的那一个不传。上传沿用每日备份的只写账号与 offsite-upload.py（只新建、不覆盖、按 md5 回读）。存储桶生命周期 15 天删
# （比每日备份多一天，任何一份每日备份都能接着回放到最新）。本机仍按 compose 保留 7 天。
# 已传的记在 /opt/pearnly/state/binlog-shipped.txt；第一次运行只传最近 26 小时内关上的（更早的已有每日备份覆盖）。
# 每次一行记进 binlog-ship.log，最近一次写 binlog-ship-last.json；甲骨文那台的守望按存储桶里最新一份判断是否停了。
set -uo pipefail
STATE=/opt/pearnly/state; LOG=$STATE/binlog-ship.log; SECRETS=/opt/pearnly/secrets; DEPLOY=/opt/pearnly/deploy
if [[ -f $SECRETS/runtime.env ]]; then
  while IFS='=' read -r key value; do [[ $key == PEARNLY_BACKUP_* ]] && export "$key=$value"; done < $SECRETS/runtime.env
fi
BUCKET=${PEARNLY_BACKUP_BUCKET:-pearnly-backups-sg}
mkdir -p $STATE
exec 9>$STATE/binlog-ship.lock
flock -n 9 || exit 0
started=$(date +%s); work=/opt/pearnly/backups/binlog
fail() {
  printf '{"at":"%s","status":"failed","step":"%s"}\n' "$(date -u +%FT%TZ)" "$1" | tee -a $LOG > $STATE/binlog-ship-last.json
  exit 1
}

M=pearnly-production-mariadb-1
NET=$(docker inspect $M --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
DBIMG=$(docker inspect $M --format '{{.Config.Image}}')
PW=$(docker inspect $M --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^MARIADB_ROOT_PASSWORD=//p')
DATADIR=$(docker inspect $M --format '{{range .Mounts}}{{if eq .Destination "/var/lib/mysql"}}{{.Source}}{{end}}{{end}}')
[[ -n $NET && -n $PW && -d $DATADIR ]] || fail inspect_database
docker run --rm --network "$NET" --memory 256m -e MYSQL_PWD="$PW" --entrypoint mariadb "$DBIMG" -h mariadb -uroot \
  -e 'FLUSH BINARY LOGS' || fail flush

mapfile -t files < <(sed 's#^\./##' "$DATADIR/binlog.index")
(( ${#files[@]} >= 2 )) || fail no_closed_binlog
shipped=$STATE/binlog-shipped.txt
if [[ ! -e $shipped ]]; then
  cutoff=$(( $(date +%s) - 26 * 3600 ))
  for f in "${files[@]:0:${#files[@]}-1}"; do
    (( $(stat -c %Y "$DATADIR/$f" 2>/dev/null || echo 0) < cutoff )) && printf '%s\n' "$f"
  done > $shipped
fi

rm -rf $work; mkdir -p $work; chmod 700 $work
count=0
for f in "${files[@]:0:${#files[@]}-1}"; do   # 最后一个是正在写的，不传
  grep -qxF "$f" $shipped && continue
  [[ -f $DATADIR/$f ]] || continue
  mtime=$(stat -c %Y "$DATADIR/$f")
  day=$(date -u -d "@$mtime" +%F); mkdir -p $work/$day
  gzip -6 -c "$DATADIR/$f" > "$work/$day/$(date -u -d "@$mtime" +%Y%m%dT%H%M%SZ)-$f.gz" || fail "gzip $f"
  printf '%s\n' "$f" >> $work/.names
  count=$(( count + 1 ))
done

bytes=0
if (( count )); then
  APPIMG=$(docker inspect pearnly-production-cowork-web-1 --format '{{.Config.Image}}')
  for dir in $work/*/; do
    day=$(basename $dir)
    docker run --rm --user 0:0 --memory 512m -v $dir:/backup:ro -v $SECRETS/backup-writer.json:/key.json:ro \
      -v $DEPLOY/scripts/production/offsite-upload.py:/upload.py:ro --entrypoint /home/frappe/frappe-bench/env/bin/python \
      "$APPIMG" /upload.py --key /key.json --bucket $BUCKET --source /backup "binlog/$day" >/dev/null || fail "upload $day"
  done
  bytes=$(du -sb --exclude=.names $work | cut -f1)
  cat $work/.names >> $shipped
fi
# 本机已按 7 天清掉的文件不必再记着
grep -xF -f <(printf '%s\n' "${files[@]}") $shipped > $shipped.tmp; mv -f $shipped.tmp $shipped
line=$(printf '{"at":"%s","status":"ok","files":%d,"bytes":%s,"seconds":%d}' \
  "$(date -u +%FT%TZ)" "$count" "$bytes" "$(( $(date +%s) - started ))")
printf '%s\n' "$line" > $STATE/binlog-ship-last.json
(( count )) && printf '%s\n' "$line" >> $LOG   # 没有新文件的那几次不记，日志一天几十行
rm -rf $work
