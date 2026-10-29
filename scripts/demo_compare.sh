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
# Enough challenges that the profiled detector is past its warm-up, or the
# comparison measures who starts faster rather than who is better.
#
# Measured crossover: at 40 challenges the naive miner wins 80.0% to 62.5%,
# at 100 it still wins 88.0% to 83.0%, and only by 200 does the profiled
# detector lead (89.0% to 84.5%). 400 leaves a comfortable margin.
CHALLENGES="${CHALLENGES:-400}"
MIN_CHALLENGES="${MIN_CHALLENGES:-200}"

if (( CHALLENGES < MIN_CHALLENGES )); then
  cat >&2 <<MSG
refusing to run a comparison with only $CHALLENGES challenges.

The per-step detector needs about 200 challenges to overtake the naive one -
below that it is still learning each step's norms and will lose, making the
better miner look worse. That is a misleading demo, not a fast one.

Use at least $MIN_CHALLENGES, or set MIN_CHALLENGES=$CHALLENGES to override
deliberately (for instance to show the warm-up itself).
MSG
  exit 1
fi

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

ARGS=(--once --organic --challenges "$CHALLENGES")

if [[ -n "${DISCOVER:-}" ]]; then
  # Find both miners by reading the metagraph instead of being told where they
  # are. Their axons are already published on chain, so nothing is republished
  # here - the chain's serving rate limit makes re-publishing on every run a
  # bad idea, and it is unnecessary when the addresses have not changed.
  #
  # Requires the ports below to match what is published, and the published IP
  # to still be this host's. WSL's IP changes across reboots, so if discovery
  # finds nothing, re-publish:
  #   btcli tx serve-axon --netuid 600 --network test --wallet flowstate \
  #     --wallet-hotkey miner --ip $(hostname -I | awk '{print $1}') --port 8091
  echo "    (discovering miners from the metagraph)"
else
  ARGS+=(--miner "miner=http://127.0.0.1:$PORT_GOOD"
         --miner "miner_naive=http://127.0.0.1:$PORT_WEAK")
fi

if [[ -n "${SET_WEIGHTS:-}" ]]; then
  ARGS+=(--set-weights)
fi

echo
echo "=== validator: $CHALLENGES scored challenges to each miner ==="
python -m neurons.validator "${ARGS[@]}"
