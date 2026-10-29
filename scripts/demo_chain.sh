#!/usr/bin/env bash
# The full on-chain FlowState loop.
#
# Unlike demo_local.sh, nothing here is hardcoded: the miner publishes its
# endpoint with the ServeAxon intent, the validator finds it by reading the
# metagraph, and the resulting scores are written to chain with SetWeights.
#
#   bash scripts/demo_chain.sh
#
# Note that ServeAxon is rate limited by the chain, so re-running this quickly
# will log a harmless warning about re-publishing too soon.

set -euo pipefail

PROJECT=/mnt/c/Users/swarn/Documents/FlowStateProject
PORT="${PORT:-8091}"
CHALLENGES="${CHALLENGES:-20}"
# The IP written to chain. 127.0.0.1 is fine while both neurons share a
# machine; a real deployment needs a routable address.
PUBLISH_IP="${PUBLISH_IP:-127.0.0.1}"

# shellcheck disable=SC1091
source "$HOME/venvs/flowstate/bin/activate"
cd "$PROJECT"

echo "=== starting miner (publishing $PUBLISH_IP:$PORT on chain) ==="
python -m neurons.miner --port "$PORT" --ip "$PUBLISH_IP" &
MINER_PID=$!
trap 'kill "$MINER_PID" 2>/dev/null || true; wait "$MINER_PID" 2>/dev/null || true' EXIT

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
echo "=== waiting for the axon to appear on chain ==="
# The chain rate limits serving, so a publish can be deferred well past the
# miner's startup. Weights do not depend on this - uid comes from registration,
# not from the axon - so fall back to a direct URL rather than skip the round.
if python -m scripts.wait_for_axon --hotkey miner --timeout "${AXON_WAIT:-90}"; then
  DISCOVERY=(--once --organic --set-weights --challenges "$CHALLENGES")
  echo "=== validator: discovering miner from the metagraph ==="
else
  DISCOVERY=(--once --organic --set-weights --challenges "$CHALLENGES"
             --miner-url "http://127.0.0.1:$PORT")
  echo "=== validator: axon not on chain yet, using direct URL ==="
  echo "    (weights still go on chain - uid comes from registration)"
fi

echo
python -m neurons.validator "${DISCOVERY[@]}"
