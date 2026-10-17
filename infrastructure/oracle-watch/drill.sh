#!/usr/bin/env bash
# 每月恢复演练（甲骨文那台，pearnly-drill.timer；用户 2026-10-17 定）。不碰生产：在这台的一次性临时数据库里恢复。
# 1) 只读账号下载最新一份每日备份和它之后的改动记录，核 SHA256
# 2) 临时 mariadb（与生产同一版本）逐库导入，再从各库导出文件里记的位置接着回放改动记录 —— 恢复到最近 5 分钟
# 3) 报告：各库表数、关键表行数、恢复到的最新时刻（离现在几分钟）；密钥包用 backup.key 解密后列目录
# 4) 删掉临时容器、卷和下载的文件；结果发邮件（成功、失败都发）
# 生产那台的 scripts/production/restore-drill.sh 仍可在需要「与生产逐表对比」时手动跑；平时的演练在这里做，不占生产内存。
set -uo pipefail
ROOT=/opt/pearnly-watch; work=/var/lib/pearnly-watch/drill; drill=pearnly-restore-drill
DBIMG=mariadb:11.8@sha256:79d59758afc91b89b120b0a8904d637f5a3b3e1c4900f29b740d6d46c72fef68
started=$(date +%s); report=/var/lib/pearnly-watch/drill-$(date -u +%F).log
mkdir -p /var/lib/pearnly-watch
exec > >(tee $report) 2>&1
cleanup() { docker rm -f $drill >/dev/null 2>&1; docker volume rm -f $drill >/dev/null 2>&1; rm -rf $work; }
finish() {
  local status=$1
  echo "== 结束：$status，耗时 $(( $(date +%s) - started )) 秒"
  sleep 1
  docker run --rm --network host -v $ROOT:/opt/pearnly-watch:ro -v $report:/report.txt:ro pearnly-watch \
    mail "Pearnly 恢复演练：$status" /report.txt || echo "邮件没发出去"
  cleanup
  [[ $status == 成功 ]]
  exit $?
}
cleanup; mkdir -p $work; chmod 700 $work
watch() { docker run --rm --network host -v $ROOT:/opt/pearnly-watch:ro -v $work:/work pearnly-watch "$@"; }

echo "== 1) 下载 $(date -u +%T) UTC"
day=$(watch fetch --dest /work | tail -1) || finish "失败（下载）"
echo "每日备份 $day；改动记录 $(ls $work/binlog | wc -l) 份；合计 $(du -sh $work | cut -f1)"
( cd $work && grep -v " SHA256SUMS$" SHA256SUMS | grep -E " (db-|secrets)" | sha256sum -c --quiet - ) \
  && echo "SHA256 全部一致" || finish "失败（校验不一致）"

echo "== 2) 导入临时库 $(date -u +%T) UTC"
pw=$(openssl rand -hex 16)
docker run -d --name $drill --memory 4g -v $drill:/var/lib/mysql -v $work:/work:ro -e MARIADB_ROOT_PASSWORD=$pw \
  "$DBIMG" --innodb-buffer-pool-size=2G --max-allowed-packet=256M >/dev/null || finish "失败（起临时库）"
for i in $(seq 1 90); do docker exec $drill healthcheck.sh --connect >/dev/null 2>&1 && break; sleep 2; done
q() { docker exec -i -e MYSQL_PWD=$pw $drill mariadb -uroot -N -B "$@"; }
# 改动记录按原编号排好、解压，回放时整条流喂给 mariadb-binlog
mkdir -p $work/plain
for f in $work/binlog/*.gz; do [[ -e $f ]] || continue; base=$(basename $f .gz); gunzip -c $f > $work/plain/${base#*-}; done
dbs=""
for f in $work/db-*.sql.gz; do
  db=$(basename $f .sql.gz); db=${db#db-}; [[ $db == mysql ]] && continue
  t0=$(date +%s); gunzip -c $f | q || finish "失败（导入 $db）"
  position=$(gunzip -c $f | head -40 | sed -n "s/^-- CHANGE MASTER TO MASTER_LOG_FILE='\([^']*\)', MASTER_LOG_POS=\([0-9]*\);.*/\1 \2/p")
  if [[ -z $position ]]; then echo "  $db 导入 $(( $(date +%s) - t0 ))s（这份备份没记位置，只能恢复到备份那一刻）"; dbs="$dbs $db"; continue; fi
  read file pos <<< "$position"
  files=$(ls $work/plain | sort | awk -v f=$file '$0>=f')
  [[ $(echo "$files" | head -1) == "$file" ]] || finish "失败（缺少 $file 起的改动记录）"
  # 编号必须连续，否则中间丢了一段
  expect=$(( 10#${file##*.} )); for g in $files; do (( 10#${g##*.} == expect )) || finish "失败（改动记录断档：缺 $expect）"; expect=$(( expect + 1 )); done
  docker exec -e MYSQL_PWD=$pw $drill sh -c "cd /work/plain && mariadb-binlog --database=$db --start-position=$pos $(echo $files) | mariadb -uroot" \
    || finish "失败（回放 $db）"
  echo "  $db 导入并回放 $(( $(date +%s) - t0 ))s（从 $file:$pos 接着回放 $(echo $files | wc -w) 份）"
  dbs="$dbs $db"
done

echo "== 3) 核对 $(date -u +%T) UTC"
key_tables="'tabUser','tabFile','tabCowork OCR History','tabCowork Bank Reconciliation','tabPearnly Wallet','tabPearnly Credit Transaction','tabErp Company','tabDMS Booking'"
latest=""
for db in $dbs; do
  [[ $db == keycloak ]] && { echo "  keycloak 表数 $(q -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='keycloak'")"; continue; }
  echo "  $db 表数 $(q -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$db'")"
  for t in $(q -e "SELECT table_name FROM information_schema.tables WHERE table_schema='$db' AND table_name IN ($key_tables)" | tr ' ' '#'); do
    t=${t//#/ }; echo "    $t $(q -e "SELECT COUNT(*) FROM \`$db\`.\`$t\`") 行"
  done
  newest=$(q -e "SELECT MAX(modified) FROM \`$db\`.\`tabVersion\`" 2>/dev/null)
  [[ -n $newest && $newest != NULL && $newest > $latest ]] && latest=$newest
done
if [[ -n $latest ]]; then
  echo "  恢复到的最新改动：$latest（各站点时区）"
fi
last_event=$(ls $work/plain | sort | tail -1)
[[ -n $last_event ]] && echo "  最后一份改动记录：$(ls $work/binlog | sort -t- -k2 | tail -1 | cut -d- -f1)（UTC，文件关上的时刻）"
if [[ -s $ROOT/secrets/backup.key ]]; then
  openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass file:$ROOT/secrets/backup.key -in $work/secrets.tar.gz.enc | tar -tz > $work/secrets.list \
    && echo "  密钥包解密成功：$(grep -c site_config.json $work/secrets.list) 份站点配置" || finish "失败（密钥包解密）"
fi
finish 成功
