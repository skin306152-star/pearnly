#!/usr/bin/env bash
# 40MB 并发上传压测时在生产上只读采样（docs/work/2026-11-11-prod-stress-round2.md「大文件并发上传」）。
# 一次 SSH 长连接跑完、到时自己退出，不在本机循环 SSH（ssh-polling-ban）；不进数据库容器、不改任何东西。
#   ssh pearnly 'bash -s' -- 420 < scripts/e2e/stress_upload_sampler.sh > samples.tsv
# 每 2 秒一行：时刻、容器、cgroup 匿名内存 MiB、memory.events 的 max 与 oom_kill、整机 swap 已用 MiB。
# 首尾各打一次 docker stats（含 CPU）与 free -m。参数 1 = 采样秒数（默认 420，最长 1200）。
set -u
SECONDS_TOTAL=${1:-420}
[ "$SECONDS_TOTAL" -gt 1200 ] && SECONDS_TOTAL=1200
P=pearnly-production
SERVICES="cowork-web erp-web"
sid() { docker ps -q --filter label=com.docker.compose.project=$P --filter label=com.docker.compose.service="$1" | head -1; }
declare -A DIR
for s in $SERVICES; do
  id=$(docker inspect -f '{{.Id}}' "$(sid "$s")" 2>/dev/null) || { echo "# 找不到 $s"; continue; }
  DIR[$s]=$(find /sys/fs/cgroup -maxdepth 3 -type d -name "docker-$id.scope" | head -1)
  echo "# $s limit=$(docker inspect -f '{{.HostConfig.Memory}}' "$(sid "$s")") cgroup=${DIR[$s]}"
done
snapshot() { echo "# $1 $(date -u +%FT%TZ)"; docker stats --no-stream --format '# {{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' $(for s in $SERVICES; do sid "$s"; done); free -m | sed 's/^/# /'; }
snapshot start
printf 'utc\tservice\tanon_mib\tmax\toom_kill\tswap_used_mib\n'
end=$(( $(date +%s) + SECONDS_TOTAL ))
while [ "$(date +%s)" -lt "$end" ]; do
  now=$(date -u +%T)
  swap=$(awk '/SwapTotal/{t=$2}/SwapFree/{f=$2}END{printf "%d", (t-f)/1024}' /proc/meminfo)
  for s in $SERVICES; do
    d=${DIR[$s]:-}; [ -n "$d" ] || continue
    printf '%s\t%s\t%d\t%s\t%s\t%s\n' "$now" "$s" $(( $(awk '$1=="anon"{print $2}' "$d/memory.stat") / 1048576 )) \
      "$(awk '$1=="max"{print $2}' "$d/memory.events")" "$(awk '$1=="oom_kill"{print $2}' "$d/memory.events")" "$swap"
  done
  sleep 2
done
snapshot end
