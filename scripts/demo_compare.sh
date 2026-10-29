#!/usr/bin/env bash
# Two miners, one validator: does FlowState actually rank anyone?
#
# Runs the real detector (per-step statistical profiling) against a
# deliberately weaker one (global thresholds, learns nothing) and shows the
# emission share each earns. Both miners are genuinely trying; the only
# difference is whether they model "slow" per step or globally.
#
# This is the run that answers "can your subnet tell a good miner from a bad
# one?" - which is the question a Protocol Track judge will actually ask.
#
#   bash scripts/demo_compare.sh                 # scored locally, no spend
#   SET_WEIGHTS=1 bash scripts/demo_compare.sh   # also commit weights on chain
#
# Needs a second hotkey:
#   btcli wallet new-hotkey --wallet flowstate --wallet-hotkey miner_naive
# and, to earn weight rather than just be scored, that hotkey registered:
#   btcli subnet register --netuid 600 --network test \
#     --wallet flowstate --wallet-hotkey miner_naive

set -euo pipefail

PROJECT=/mnt/c/Users/swarn/Documents/FlowStateProject
PORT_GOOD="${PORT_GOOD:-8091}"
PORT_WEAK="${PORT_WEAK:-8092}"
# Enough rounds that both miners pass the profiled detector's 30-session
# warm-up, or the comparison just measures who starts faster.
CHALLENGES="${CHALLENGES:-400}"

# shellcheck disable=SC1091
source "$HOME/venvs/flowstate/bin/activate"
cd "$PROJECT"

PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT

start_miner() {
  local hotkey=$1 port=$2 detector=$3
  echo "=== starting $detector miner (hotkey $hotkey) on port $port ==="
  python -m neurons.miner \
    --hotkey "$hotkey" \
    --port "$port" \
    --detector "$detector" \
    --no-serve-axon \
    --skip-registration-check &
  PIDS+=("$!")
}

wait_for_port() {
  local port=$1
  for _ in $(seq 1 60); do
    if (echo >"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      return 0
    fi
    sleep 0.5
  done
  echo "nothing came up on port $port" >&2
  return 1
}

start_miner miner       "$PORT_GOOD" profile
start_miner miner_naive "$PORT_WEAK" naive

wait_for_port "$PORT_GOOD"
wait_for_port "$PORT_WEAK"
echo "=== both miners accepting connections ==="

ARGS=(--once --organic --challenges "$CHALLENGES"
      --miner "miner=http://127.0.0.1:$PORT_GOOD"
      --miner "miner_naive=http://127.0.0.1:$PORT_WEAK")

if [[ -n "${SET_WEIGHTS:-}" ]]; then
  ARGS+=(--set-weights)
fi

echo
echo "=== validator: $CHALLENGES scored challenges to each miner ==="
python -m neurons.validator "${ARGS[@]}"
