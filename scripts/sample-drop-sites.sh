#!/usr/bin/env bash
# 拆掉样板租户的站点（drop-site）：**先把备份拉到宿主机**，再删。
#
# 用法: scripts/sample-drop-sites.sh <组> <站点,逗号分开> [备份目录]
#   scripts/sample-drop-sites.sh dms flowu1.localhost,flowdms.localhost
#   scripts/sample-drop-sites.sh cowork qc65956.localhost
#
# 备份落在 `<备份目录>/<站点>/`（默认 `~/.config/pearnly/backups/sites/<日期>/`），
# 之后 drop 用 `--no-backup` —— 不做第二遍，省得两处各存一份又说不清在哪。
#
# ⚠️ 别拿它删 `dms-a.localhost` / `dms-b.localhost` 这类样板站 ——
# 那些是切片验证的地基，不是租户。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"

group="${1:?用法: $0 <组> <站点,逗号分开> [备份目录]}"
sites_arg="${2:?用法: $0 <组> <站点,逗号分开> [备份目录]}"
dest="${3:-$HOME/.config/pearnly/backups/sites/$(date +%Y%m%d-%H%M%S)}"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/03-wekan.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done
dc() { docker --context "$CTX" compose "${args[@]}" "$@"; }

# 根密码从 compose 里读，不写死第二份
DB_ROOT_PASS="$(sed -n 's/.*MARIADB_ROOT_PASSWORD:[[:space:]]*//p' "$HERE/infrastructure/compose/00-base.yml" | head -1 | tr -d '"'"'"' \r')"
if [ -z "$DB_ROOT_PASS" ]; then
  echo "[失败] 读不到 MARIADB_ROOT_PASSWORD" >&2
  exit 1
fi

container="pearnly-sample-${group}-bench-1"
mkdir -p "$dest"
echo "备份目录: $dest"

IFS=',' read -r -a sites <<< "$sites_arg"
for site in "${sites[@]}"; do
  site="$(printf '%s' "$site" | tr -d ' ')"
  [ -n "$site" ] || continue

  if ! dc exec -T "$group-bench" test -d "/home/frappe/frappe-bench/sites/$site"; then
    echo "  - ${site}：站点不存在，跳过"
    continue
  fi

  # ${} 不能省：变量后面直接跟中文时，全角字符的字节会被 bash 当成变量名的一部分
  echo "  → ${site}：备份"
  dc exec -T "$group-bench" bench --site "$site" backup --with-files >/dev/null
  mkdir -p "$dest/$site"
  docker --context "$CTX" cp \
    "$container:/home/frappe/frappe-bench/sites/$site/private/backups/." "$dest/$site/" >/dev/null

  # 备份为空（或没拉到）就不删 —— 宁可留着，也不要删掉拉不出来的东西
  if ! ls "$dest/$site"/*.sql.gz >/dev/null 2>&1; then
    echo "    ! 没拿到 .sql.gz，**不删**这个站点" >&2
    continue
  fi

  echo "    → drop"
  dc exec -T "$group-bench" bench drop-site "$site" \
    --db-root-username root --db-root-password "$DB_ROOT_PASS" --force --no-backup >/dev/null
  echo "    ✓ ${site} 已拆（备份在 ${dest}/${site}）"
done

echo "[done]"
