#!/usr/bin/env bash
# before · after · leak 세 번의 감사를 차례로 돌리고 reports/<run-id>/에 묶는다.
#   RUN_ID=kind-20261003 IDLE_SECONDS=600 scripts/run_all.sh [profiles...]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUN_ID="${RUN_ID:-kind-$(date -u +%Y%m%d)}"
PROFILES=("${@:-before after leak}")
read -r -a PROFILES <<< "${PROFILES[*]}"

for p in "${PROFILES[@]}"; do
  "$ROOT/scripts/audit.sh" "$p" "$ROOT/results/$RUN_ID/$p"
done

args=()
for p in before after leak; do
  if [[ -f "$ROOT/results/$RUN_ID/$p/findings.csv" ]]; then
    args+=(--run "$p=$ROOT/results/$RUN_ID/$p")
  fi
done
cd "$ROOT"
uv run egress-audit report --out "reports/$RUN_ID" --title "외부 호출 감사 결과: $RUN_ID (kind on macOS, colima)" "${args[@]}"
