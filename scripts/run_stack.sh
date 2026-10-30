#!/usr/bin/env bash
# Bring the whole live stack up: two miners and the telemetry gateway.
#
#   bash scripts/run_stack.sh          # then open http://127.0.0.1:8099
#   HOST=0.0.0.0 bash scripts/run_stack.sh    # if localhost forwarding fails
#
# Runs in the foreground and holds the processes; Ctrl-C stops all three.
# Nothing here touches the chain beyond reading the metagraph and weights, and
# organic sessions are never scored, so this is safe to leave running.

set -uo pipefail

PROJECT=/mnt/c/Users/swarn/Documents/FlowStateProject
PORT_GOOD="${PORT_GOOD:-8091}"
PORT_WEAK="${PORT_WEAK:-8092}"
PORT_GW="${PORT_GW:-8099}"
# The browser leg carries no authentication, so loopback is the default. Bind
# wider only deliberately, and only on a network you trust.
HOST="${HOST:-127.0.0.1}"

# shellcheck disable=SC1091
source "$HOME/venvs/flowstate/bin/activate"
cd "$PROJECT" || exit 1

PIDS=()
cleanup() {
  echo ""
  echo "stopping..."
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  for p in "${PIDS[@]:-}"; do wait "$p" 2>/dev/null || true; done
}
trap cleanup EXIT INT TERM

wait_port() {
  for _ in $(seq 1 120); do
    (echo >"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0
    sleep 0.5
  done
  return 1
}

echo "=== miner: per-step profile  (uid 1) on $PORT_GOOD ==="
python -m neurons.miner --hotkey miner --port "$PORT_GOOD" \
  --detector profile --no-serve-axon > /tmp/flowstate-miner-profile.log 2>&1 &
PIDS+=("$!")

echo "=== miner: naive thresholds  (uid 2) on $PORT_WEAK ==="
python -m neurons.miner --hotkey miner_naive --port "$PORT_WEAK" \
  --detector naive --no-serve-axon > /tmp/flowstate-miner-naive.log 2>&1 &
PIDS+=("$!")

wait_port "$PORT_GOOD" && echo "  miner uid 1 listening" || echo "  miner uid 1 FAILED"
wait_port "$PORT_WEAK" && echo "  miner uid 2 listening" || echo "  miner uid 2 FAILED"

echo "=== gateway on $HOST:$PORT_GW ==="
python -m neurons.gateway --host "$HOST" --port "$PORT_GW" \
  --miner "miner=http://127.0.0.1:$PORT_GOOD" \
  --miner "miner_naive=http://127.0.0.1:$PORT_WEAK" > /tmp/flowstate-gateway.log 2>&1 &
PIDS+=("$!")

if wait_port "$PORT_GW"; then
  echo "  gateway listening"
else
  echo "  gateway FAILED - last lines of its log:"
  tail -20 /tmp/flowstate-gateway.log
  exit 1
fi

echo ""
echo "  open  http://127.0.0.1:$PORT_GW"
echo "  keys  0 none · 1 stall · 2 error_loop · 3 rage_click · 4 silent_failure"
echo "  logs  /tmp/flowstate-gateway.log  /tmp/flowstate-miner-*.log"
echo ""
echo "running. Ctrl-C to stop all three."
wait
