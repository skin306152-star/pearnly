#!/usr/bin/env bash
# 把产品 App 装到对应站点：每组一个产品，两个租户站点共用同一个产品 App。
#
# 用法: scripts/sample-install-products.sh
set -euo pipefail

COMPOSE=infrastructure/compose/01-bench.yml
CTX=colima-pearnly

d() { docker --context "$CTX" "$@"; }
dc() { d compose -f "$COMPOSE" "$@"; }

# 组:产品App:站点列表
# 注意：变量别叫 GROUPS —— 那是 zsh 的内置数组，会串味。
PRODUCT_GROUPS=(
  "cowork:pearnly_cowork:cowork-a.localhost cowork-b.localhost"
  "erp:pearnly_erp_portal:erp-a.localhost erp-b.localhost"
  "dms:pearnly_dms:dms-a.localhost dms-b.localhost"
  "control:pearnly_control:control.localhost"
)

for entry in "${PRODUCT_GROUPS[@]}"; do
  IFS=':' read -r group product sites <<< "$entry"
  bench="$group-bench"
  echo "=== 组 $group -> $product"

  # apps.txt 必须整份重写（镜像里那份没有结尾换行，追加会拼串）
  dc exec -T "$bench" bash -c \
    "printf 'frappe\nerpnext\npearnly_core\n$product\n' > /home/frappe/frappe-bench/sites/apps.txt"

  for site in $sites; do
    for app in pearnly_core "$product"; do
      if dc exec -T "$bench" bench --site "$site" list-apps 2>/dev/null | grep -q "^\s*$app "; then
        echo "    [$site] $app 已装"
      else
        dc exec -T "$bench" bench --site "$site" install-app "$app" >/dev/null 2>&1
        echo "    [$site] $app 已安装"
      fi
    done
  done
done

echo "[done]"
