#!/bin/sh
# LINE sign-in E2E laboratory (docs/work/2026-09-25-erp-line-intake.md §7).
#
# Real Frappe sites (erp.lab + billing.lab member service) and a real Keycloak 26.7.4 configured by
# scripts/production/configure-line-login.py; LINE itself is a fake OIDC realm. Synthetic data only,
# no host ports. Needs its own Docker context so the shared sample is never touched, e.g.:
#   colima start pearnly-line-login --cpu 4 --memory 6 --disk 30 && docker context use colima-pearnly
#   PEARNLY_LINE_LOGIN_LAB_CONTEXT=colima-pearnly-line-login sh scripts/line-login-lab.sh all
# The images (pearnly/frappe-sample:16.34.1, keycloak 26.7.4, mariadb 11.8, redis 6.2) must be loaded there.
set -eu
CONTEXT=${PEARNLY_LINE_LOGIN_LAB_CONTEXT:?set the dedicated lab Docker context}
case "$CONTEXT" in colima-pearnly|default|desktop-linux) echo "refusing shared context $CONTEXT" >&2; exit 2;; esac
cd "$(dirname "$0")/.."
dc() { docker --context "$CONTEXT" compose -f infrastructure/compose/line-login-lab.yml "$@"; }
in_bench() { dc exec -T bench sh -c "cd /home/frappe/frappe-bench && $1"; }
py() { dc exec -T bench /home/frappe/frappe-bench/env/bin/python "/lab/scripts/$1"; }

up() {
  dc up -d
  in_bench 'printf "frappe\nerpnext\npearnly_core\npearnly_dms\npearnly_cowork\npearnly_erp_portal\npearnly_control\n" > sites/apps.txt
    env/bin/python -c "import json;p=\"sites/common_site_config.json\";d=json.load(open(p));d.update(db_host=\"mariadb\",db_port=3306,redis_cache=\"redis://redis-cache:6379\",redis_queue=\"redis://redis-queue:6379\",redis_socketio=\"redis://redis-queue:6379\",serve_default_site=False);json.dump(d,open(p,\"w\"))"
    for s in erp.lab billing.lab; do [ -d sites/$s ] || bench new-site $s --db-root-password lab-root-only --admin-password lab-admin-only --install-app pearnly_core; done
    bench --site billing.lab list-apps | grep -q pearnly_control || bench --site billing.lab install-app pearnly_control
    bench --site erp.lab migrate && bench --site billing.lab migrate'
}
serve() {
  dc exec -T -d bench sh -c 'cd /home/frappe/frappe-bench && exec bench serve --port 8000 > /tmp/web.log 2>&1'
  dc exec -T -d bench sh -c 'cd /home/frappe/frappe-bench && exec bench worker --queue short,default > /tmp/worker.log 2>&1'
  sleep 8
}
case "${1:-all}" in
  up) up ;;
  setup) py line-login-lab-keycloak.py && py line-login-lab-sites.py ;;
  serve) serve ;;
  verify) py line-login-lab-verify.py ;;
  all) up && py line-login-lab-keycloak.py && py line-login-lab-sites.py && serve && py line-login-lab-verify.py ;;
  down) dc down --volumes ;;
  *) echo "usage: $0 up|setup|serve|verify|all|down" >&2; exit 2 ;;
esac
