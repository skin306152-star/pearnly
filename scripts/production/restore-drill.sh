#!/usr/bin/env bash
# 恢复演练（每月一次，及每次改备份方案后；宿主机 root）。不碰生产库：在一次性临时数据库里恢复，再和生产只读对比。
# 1) 用只读服务账号下载 gs://pearnly-backups-sg 最新一份，核 SHA256SUMS
# 2) 起一个临时 mariadb（2G 内存、不开端口），逐库导入
# 3) 每个库的表数，与关键表行数：备份里 vs 生产现在（生产只会多不会少）；文件包 tar -t、密钥包解密后 tar -t
# 4) 在恢复出的库里抽读一张票、一份银行对账及其行（等同于在临时站点打开它们）
# 5) 记下耗时与结果，删掉临时容器、卷和下载的文件
# 输出写到 /opt/pearnly/state/restore-drill-<日期>.log。用法：restore-drill.sh [YYYY-MM-DD]
set -uo pipefail
STATE=/opt/pearnly/state; SECRETS=/opt/pearnly/secrets; DEPLOY=/opt/pearnly/deploy
BUCKET=${PEARNLY_BACKUP_BUCKET:-pearnly-backups-sg}; work=/opt/pearnly/backups/drill; drill=pearnly-restore-drill
started=$(date +%s); out=$STATE/restore-drill-$(date -u +%F).log
exec > >(tee $out) 2>&1
cleanup() { docker rm -f $drill >/dev/null 2>&1; docker volume rm -f $drill >/dev/null 2>&1; rm -rf $work; }
trap cleanup EXIT
cleanup; mkdir -p $work; chmod 700 $work
echo "== 1) 下载 $(date -u +%T)"
APPIMG=$(docker inspect pearnly-production-cowork-web-1 --format '{{.Config.Image}}')
day=$(docker run --rm --user 0:0 --memory 512m -v $work:/dest -v $SECRETS/backup-reader.json:/key.json:ro \
  -v $DEPLOY/scripts/production/offsite-download.py:/download.py:ro --entrypoint /home/frappe/frappe-bench/env/bin/python \
  "$APPIMG" /download.py --key /key.json --bucket $BUCKET --dest /dest ${1:+--day $1}) || { echo "下载失败"; exit 1; }
echo "备份日期 $day，文件 $(ls $work | wc -l) 个，$(du -sh $work | cut -f1)"
# 跳过清单自己那一行（2026-10-02 第一份备份的清单误把自己算了进去）
( cd $work && grep -v " SHA256SUMS$" SHA256SUMS | sha256sum -c --quiet - ) && echo "SHA256 全部一致" || { echo "SHA256 不一致"; exit 1; }
echo "== 2) 导入临时库 $(date -u +%T)"
M=pearnly-production-mariadb-1; DBIMG=$(docker inspect $M --format '{{.Config.Image}}'); pw=$(openssl rand -hex 16)
docker run -d --name $drill --memory 2g -v $drill:/var/lib/mysql -e MARIADB_ROOT_PASSWORD=$pw "$DBIMG" \
  --innodb-buffer-pool-size=1G --max-allowed-packet=64M >/dev/null
for i in $(seq 1 60); do docker exec $drill healthcheck.sh --connect >/dev/null 2>&1 && break; sleep 2; done
q() { docker exec -i -e MYSQL_PWD=$pw $drill mariadb -uroot -N -B "$@"; }
for f in $work/db-*.sql.gz; do db=$(basename $f .sql.gz); db=${db#db-}; [[ $db == mysql ]] && continue
  t0=$(date +%s); gunzip -c $f | q || { echo "导入 $db 失败"; exit 1; }; echo "  导入 $db $(( $(date +%s)-t0 ))s"; done
echo "== 3) 对比 $(date -u +%T)"
NET=$(docker inspect $M --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
PPW=$(docker inspect $M --format '{{range .Config.Env}}{{println .}}{{end}}' | sed -n 's/^MARIADB_ROOT_PASSWORD=//p')
live() { docker run --rm -i --network "$NET" --memory 256m -e MYSQL_PWD="$PPW" --entrypoint mariadb "$DBIMG" -h mariadb -uroot -N -B "$@"; }
key_tables="'tabUser','tabFile','tabCowork OCR History','tabCowork Bank Reconciliation','tabCowork Bank Reconciliation Row','tabCowork Income Reconciliation','tabPearnly Wallet','tabPearnly Credit Transaction','tabErp Company','tabDMS Booking'"
for f in $work/db-*.sql.gz; do db=$(basename $f .sql.gz); db=${db#db-}; [[ $db =~ ^(mysql|keycloak)$ ]] && continue
  restored=$(q -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$db'"); current=$(live -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='$db'")
  echo "  $db 表数 备份 $restored / 生产 $current"
  for t in $(q -e "SELECT table_name FROM information_schema.tables WHERE table_schema='$db' AND table_name IN ($key_tables)" | tr ' ' '#'); do
    t=${t//#/ }; a=$(q -e "SELECT COUNT(*) FROM \`$db\`.\`$t\`"); b=$(live -e "SELECT COUNT(*) FROM \`$db\`.\`$t\`")
    flag=""; (( a > b )) && flag="  ← 备份比生产多，检查"; echo "    $t 备份 $a / 生产 $b$flag"; done
done
for f in $work/files-*.tar.gz; do [[ -e $f ]] || continue; echo "  $(basename $f) 文件 $(tar -tzf $f | wc -l) 个，可读"; done
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass file:$SECRETS/backup.key -in $work/secrets.tar.gz.enc | tar -tz > $work/secrets.list \
  && echo "  密钥包解密成功：$(grep -c site_config.json $work/secrets.list) 份站点配置，runtime.env $(grep -c runtime.env $work/secrets.list) 份" \
  || { echo "密钥包解密失败"; exit 1; }
echo "== 4) 抽读 $(date -u +%T)"
for db in $(q -e "SELECT table_schema FROM information_schema.tables WHERE table_name='tabCowork OCR History'"); do
  echo "  $db 票：$(q -e "SELECT CONCAT(name,' ',IFNULL(invoice_no,''),' ',IFNULL(grand_total,'')) FROM \`$db\`.\`tabCowork OCR History\` ORDER BY creation DESC LIMIT 1" 2>&1 | head -1)"
  task=$(q -e "SELECT name FROM \`$db\`.\`tabCowork Bank Reconciliation\` ORDER BY creation DESC LIMIT 1" 2>/dev/null)
  [[ -n $task ]] && echo "  $db 银行对账 $task：$(q -e "SELECT COUNT(*) FROM \`$db\`.\`tabCowork Bank Reconciliation Row\` WHERE task='$task'") 行"
done
echo "== 5) 完成：耗时 $(( $(date +%s) - started )) 秒，日志 $out"
