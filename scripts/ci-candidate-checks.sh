#!/usr/bin/env bash
# Candidate-image checks for build-production.yml, run after the image is built and before it is published.
#
# Three independent lanes: offline checks in throwaway containers, the DMS flow laboratory and the DMS
# snapshot laboratory. The two laboratories use separate compose projects with no host ports, shared
# volumes or networks, so `parallel` runs all three at once: the stage takes as long as the slowest lane
# instead of the sum. The image is still published only when every lane passes (the publish step comes
# after this one), and the workflow's `always()` steps remove both laboratories.
#
# Usage: IMAGE=<candidate tag> scripts/ci-candidate-checks.sh parallel|offline|flow|snapshot
set -euo pipefail
: "${IMAGE:?set IMAGE to the candidate image}"
cd "$(dirname "$0")/.."
PY=/home/frappe/frappe-bench/env/bin/python
FLOW=(docker compose -p pearnly-dms-flow-lab -f infrastructure/compose/dms-flow-lab.yml)
SNAPSHOT=(docker compose -p pearnly-dms-snapshot-lab -f infrastructure/compose/dms-snapshot-lab.yml)
export PEARNLY_FLOW_LAB_IMAGE="$IMAGE" PEARNLY_SNAPSHOT_LAB_IMAGE="$IMAGE"

# Every command is timed, so a slow lane shows where its time went.
run() { local start=$SECONDS; "$@"; echo "  (+$((SECONDS - start))s) ${*: -1}"; }

offline_py() { run docker run --rm --network none --entrypoint "$PY" "$@"; }

offline() {
  offline_py -v "$PWD:/candidate:ro" "$IMAGE" /candidate/tests/test_cloud_onboarding.py
  offline_py -v "$PWD:/candidate:ro" \
    -e PYTHONPATH=/candidate/apps/pearnly_dms:/candidate/apps/pearnly_core:/candidate/apps/pearnly_control \
    "$IMAGE" /candidate/tests/test_dms_entrypoints.py
  offline_py "$IMAGE" -m unittest pearnly_erp_portal.integrations.test_line_intake pearnly_erp_portal.team.test_identity \
    pearnly_control.account.test_member_service pearnly_core.account.test_line_login pearnly_core.ocr_models.test_orient \
    pearnly_core.account.test_native_account_password
  for script in scripts/test-dms-line-runtime.py scripts/test-dms-intake-queue.py scripts/test-dms-query-output.py \
                tests/test_teardown_members.py scripts/test-dms-query-snapshots.py scripts/test-dms-snapshot-consumers.py \
                tests/test_cowork_no_company_scope.py tests/test_cowork_line_intake.py; do
    offline_py -v "$PWD:/candidate:ro" "$IMAGE" "/candidate/$script"
  done
}

flow() {
  run "${FLOW[@]}" up -d --wait db redis bench
  for script in dms-flow-lab-init.py test-line-usage-redis.py test-dms-native-flow.py test-dms-native-recovery.py; do
    run "${FLOW[@]}" exec -T bench env/bin/python "/workspace/scripts/$script"
  done
  run "${FLOW[@]}" exec -T -w /home/frappe/frappe-bench/sites bench "$PY" -c 'import frappe,runpy; frappe.init(site="dms-flow-lab.localhost"); frappe.connect(); frappe.set_user("Administrator"); runpy.run_path("/workspace/scripts/sample-verify-dms-booking-flow.py")["main"]()'
}

snapshot() {
  run "${SNAPSHOT[@]}" up -d --wait db redis bench
  for script in dms-snapshot-lab-init.py test-dms-snapshot-store-native.py test-dms-snapshot-reads-native.py \
                test-dms-manual-native.py test-dms-snapshot-queue-native.py; do
    run "${SNAPSHOT[@]}" exec -T bench env/bin/python "/workspace/scripts/$script"
  done
}

parallel() {
  local logs lane i status=0 result
  local lanes=(offline flow snapshot) pids=()
  logs=$(mktemp -d)
  for lane in "${lanes[@]}"; do
    ( SECONDS=0; bash "$0" "$lane"; echo "[$lane] finished in ${SECONDS}s" ) > "$logs/$lane.log" 2>&1 &
    pids+=("$!")
  done
  for i in "${!lanes[@]}"; do
    lane=${lanes[$i]}
    if wait "${pids[$i]}"; then result=passed; else result=FAILED; status=1; fi
    echo "::group::$lane lane $result"
    cat "$logs/$lane.log"
    echo "::endgroup::"
    [ "$result" = passed ] || echo "::error::candidate check lane '$lane' failed (log above)"
  done
  rm -rf "$logs"
  return "$status"
}

case "${1:-}" in
  parallel|offline|flow|snapshot) "$1" ;;
  *) echo "usage: IMAGE=<image> $0 parallel|offline|flow|snapshot" >&2; exit 2 ;;
esac
