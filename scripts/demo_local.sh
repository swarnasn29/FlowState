#!/usr/bin/env bash
# Full FlowState loop on one machine, with no on-chain spend.
#
# Starts a miner, waits for it to accept connections, runs one scored round of
# validator challenges against it plus one unscored organic session, then shuts
# the miner down. The validator still reads the real metagraph from
# test.finney, so this exercises everything except publishing an axon and
# writing weights.
#
#   bash scripts/demo_local.sh

set -euo pipefail

PROJECT=/mnt/c/Users/swarn/Documents/FlowStateProject
PORT="${PORT:-8091}"
CHALLENGES="${CHALLENGES:-20}"

# shellcheck disable=SC1091
source "$HOME/venvs/flowstate/bin/activate"
cd "$PROJECT"

echo "=== starting miner on port $PORT ==="
python -m neurons.miner \
  --port "$PORT" \
  --no-serve-axon \
  --skip-registration-check &
MINER_PID=$!
trap 'kill "$MINER_PID" 2>/dev/null || true; wait "$MINER_PID" 2>/dev/null || true' EXIT

# Wait for the port to accept connections. Uses bash's own /dev/tcp so this
# needs neither curl nor netcat.
for _ in $(seq 1 60); do
  if (echo >"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    echo "=== miner is accepting connections ==="
    break
  fi
  sleep 0.5
done

if ! (echo >"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
  echo "miner failed to come up on port $PORT" >&2
  exit 1
fi

echo
echo "=== running one scored round + organic session ==="
python -m neurons.validator \
  --once \
  --organic \
  --challenges "$CHALLENGES" \
  --miner "miner=http://127.0.0.1:$PORT"
