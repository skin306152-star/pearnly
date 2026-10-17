#!/usr/bin/env bash
# 宿主 swap（幂等）：给容器内存上限的超配部分兜底（infrastructure/production/capacity.py 要求上限之和 ≤ 物理内存 + swap）。
# 10-16 部署时上限合计 38.5 GiB、整机 32 GiB、没有 swap，激活间后台任务一起涨内存，整机挤满约 10 分钟。
# 已有足够 swap 时什么都不改；只建 /swapfile-pearnly 这一个文件，不动别的 swap。swappiness 设 10：平时不换出，
# 只在内存真紧张时把冷页换出去，换出比整机卡死好。
set -euo pipefail
[[ "$EUID" == 0 ]] || exit 2
size_gib=${PEARNLY_SWAP_GIB:-16}
[[ "$size_gib" =~ ^[0-9]+$ && "$size_gib" -ge 1 && "$size_gib" -le 64 ]] || exit 2
file=/swapfile-pearnly
swap_kib=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo)
if (( swap_kib < size_gib * 1024 * 1024 * 9 / 10 )); then
  if ! swapon --show=NAME --noheadings | grep -qx "$file"; then
    # 磁盘至少留 20 GiB 给镜像、备份与日志。
    free_kib=$(df --output=avail -k / | tail -1)
    (( free_kib > (size_gib + 20) * 1024 * 1024 )) || { echo "Not enough disk for ${size_gib} GiB swap" >&2; exit 1; }
    [[ -e "$file" ]] || fallocate -l "${size_gib}G" "$file"
    chmod 600 "$file"
    mkswap "$file" >/dev/null
    swapon "$file"
  fi
fi
grep -q "^$file " /etc/fstab || printf '%s none swap sw 0 0\n' "$file" >> /etc/fstab
printf 'vm.swappiness = 10\n' > /etc/sysctl.d/60-pearnly-swap.conf
sysctl -q -w vm.swappiness=10
swapon --show
