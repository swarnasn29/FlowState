#!/usr/bin/env bash
# Pre-flight check. Run this before a live demo.
#
# Every check is offline except the live smoke test, which reads the metagraph
# but writes nothing to chain and spends nothing.
#
#   bash scripts/healthcheck.sh
#
# Binds ports 8095/8096 by default rather than the demo ports, so it is safe to
# run while a demo or a queued weight commit is in flight.

set -uo pipefail

PROJECT=/mnt/c/Users/swarn/Documents/FlowStateProject
PORT_A="${PORT_A:-8095}"
PORT_B="${PORT_B:-8096}"
SMOKE_CHALLENGES="${SMOKE_CHALLENGES:-40}"

# shellcheck disable=SC1091
source "$HOME/venvs/flowstate/bin/activate"
cd "$PROJECT" || exit 1

PASS=0
FAIL=0
RESULTS=()

record() {
  local name=$1 status=$2 detail=${3:-}
  if [[ $status -eq 0 ]]; then
    PASS=$((PASS + 1))
    RESULTS+=("PASS  $name  $detail")
  else
    FAIL=$((FAIL + 1))
    RESULTS+=("FAIL  $name  $detail")
  fi
}

run_check() {
  local name=$1; shift
  local out
  out=$("$@" 2>&1)
  local code=$?
  if [[ $code -ne 0 ]]; then
    echo "--- $name failed ---"
    echo "$out" | tail -20
  fi
  record "$name" "$code"
}

echo "=== 1. every module compiles ==="
run_check "compile all modules" python -m compileall -q flowstate neurons tests scripts

echo "=== 2. every module imports ==="
python - <<'PY'
import sys, traceback
mods = ("flowstate.telemetry", "flowstate.detect", "flowstate.detect_naive",
        "flowstate.protocol", "neurons.miner", "neurons.validator")
bad = []
for mod in mods:
    try:
        __import__(mod)
    except Exception:
        bad.append(mod)
        traceback.print_exc()
print(f"imported {len(mods) - len(bad)}/{len(mods)}")
sys.exit(1 if bad else 0)
PY
record "import all modules" $?

echo "=== 3. wallet keys present ==="
python - <<'PY'
import sys
import bittensor as bt
missing = []
for hotkey in ("validator", "miner", "miner_naive"):
    try:
        bt.Wallet(name="flowstate", hotkey=hotkey).hotkey.ss58_address
    except Exception as exc:
        missing.append(f"{hotkey}: {type(exc).__name__}")
if missing:
    print("missing:", "; ".join(missing))
    sys.exit(1)
print("validator, miner, miner_naive all readable")
PY
record "wallet hotkeys readable" $?

echo "=== 4. generator invariants ==="
python - <<'PY'
import sys
from flowstate.telemetry import (
    AWAIT_STEPS, FRICTION_KINDS, SWAP_FLOW, generate_organic, generate_task,
)

problems = []
steps = [name for name, _, _ in SWAP_FLOW]

for seed in range(500):
    challenge, answer = generate_task(seed=seed)
    events = challenge["events"]

    if len(events) != len(SWAP_FLOW):
        problems.append(f"seed {seed}: {len(events)} events, expected {len(SWAP_FLOW)}")
    if [e["step"] for e in events] != steps:
        problems.append(f"seed {seed}: step order differs from SWAP_FLOW")
    if [e["index"] for e in events] != list(range(len(events))):
        problems.append(f"seed {seed}: indices not 0..n-1")

    # Timeline must stay consistent with dwell times after injection.
    clock = 0
    for event in events:
        if event["t_ms"] != clock:
            problems.append(f"seed {seed}: t_ms desync at index {event['index']}")
            break
        clock += event["dwell_ms"]

    findings = answer["findings"]
    if len(findings) > 2:
        problems.append(f"seed {seed}: {len(findings)} frictions, expected at most 2")
    if len({f["index"] for f in findings}) != len(findings):
        problems.append(f"seed {seed}: two frictions injected on one step")
    for finding in findings:
        idx = finding["index"]
        if not 0 <= idx < len(events):
            problems.append(f"seed {seed}: index {idx} out of range")
            continue
        if finding["kind"] not in FRICTION_KINDS:
            problems.append(f"seed {seed}: unknown kind {finding['kind']}")
        # silent_failure can only land on a step that talks to the chain.
        if finding["kind"] == "silent_failure" and events[idx]["step"] not in AWAIT_STEPS:
            problems.append(f"seed {seed}: silent_failure on {events[idx]['step']}")
    # Only AWAIT_STEPS carry chain data.
    for event in events:
        has_chain = event["chain"] is not None
        should = event["step"] in AWAIT_STEPS
        if has_chain != should:
            problems.append(f"seed {seed}: chain presence wrong on {event['step']}")
            break

    # The wire payload must not leak the answer.
    for key in ("findings", "friction_index", "friction_kind", "answer", "label"):
        if key in challenge:
            problems.append(f"seed {seed}: challenge leaks {key!r}")

# Organic sessions must be shaped identically to scored ones.
organic = generate_organic(seed=1)
if set(organic) != {"session_id", "flow", "steps", "events"}:
    problems.append(f"organic keys differ: {sorted(organic)}")

if problems:
    for line in problems[:10]:
        print(" ", line)
    print(f"{len(problems)} problem(s)")
    sys.exit(1)
counts = {}
for seed in range(500):
    _, answer = generate_task(seed=seed)
    n = len(answer["findings"])
    counts[n] = counts.get(n, 0) + 1
print("500 sessions: event count, order, indices, timeline, chain placement,")
print("distinct friction steps, silent_failure placement, answer-leak: all clean")
print("friction counts seen:", {n: counts[n] for n in sorted(counts)})
PY
record "token-swap data structures" $?

echo "=== 5. protocol round-trip ==="
python - <<'PY'
import sys
from flowstate.protocol import (
    MAX_FINDINGS, DetectRequest, DetectResponse, canonical_body,
)
from flowstate.telemetry import generate_task

problems = []
challenge, _ = generate_task(seed=11)
request = DetectRequest(**{k: challenge[k] for k in
                           ("session_id", "flow", "steps", "events")})

# Canonical bytes must be stable: the signature covers them exactly.
if canonical_body(request) != canonical_body(request):
    problems.append("canonical_body is not deterministic")
if canonical_body(request) != canonical_body(request.model_dump()):
    problems.append("canonical_body differs between model and dict")

# Reordering a dict must not change the bytes.
payload = request.model_dump()
reordered = {k: payload[k] for k in reversed(list(payload))}
if canonical_body(payload) != canonical_body(reordered):
    problems.append("canonical_body depends on key order")

try:
    DetectResponse(findings=[{"index": -1, "kind": "stall"}])
    problems.append("DetectResponse accepted a negative index")
except Exception:
    pass
try:
    DetectResponse(findings=[{"index": 0, "kind": "stall", "confidence": 5.0}])
    problems.append("DetectResponse accepted confidence > 1")
except Exception:
    pass
try:
    DetectResponse(findings=[{"index": i, "kind": "stall"}
                             for i in range(MAX_FINDINGS + 1)])
    problems.append(f"DetectResponse accepted more than {MAX_FINDINGS} findings")
except Exception:
    pass
# An empty response is a valid answer, not a malformed one: most sessions
# are clean, and a miner must be able to say so.
try:
    DetectResponse(findings=[])
except Exception:
    problems.append("DetectResponse rejected an empty findings list")

if problems:
    for line in problems:
        print(" ", line)
    sys.exit(1)
print("canonical bytes stable and order-independent; findings validation,")
print(f"the {MAX_FINDINGS}-finding cap and the empty-response case all hold")
PY
record "protocol round-trip" $?

echo "=== 6. signed-request handshake ==="
run_check "auth handshake (7 cases)" python -m tests.test_auth_loop

echo "=== 7. live two-miner smoke test ==="
PIDS=()
cleanup() {
  for pid in "${PIDS[@]:-}"; do
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT

for spec in "miner:$PORT_A:profile" "miner_naive:$PORT_B:naive"; do
  IFS=: read -r hotkey port detector <<<"$spec"
  python -m neurons.miner --hotkey "$hotkey" --port "$port" \
    --detector "$detector" --no-serve-axon --skip-registration-check \
    >/dev/null 2>&1 &
  PIDS+=("$!")
done

up=0
for port in "$PORT_A" "$PORT_B"; do
  for _ in $(seq 1 60); do
    if (echo >"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
      up=$((up + 1)); break
    fi
    sleep 0.5
  done
done
record "both miners bind and listen" $([[ $up -eq 2 ]] && echo 0 || echo 1) "$up/2 up"

if [[ $up -eq 2 ]]; then
  SMOKE=$(python -m neurons.validator --once --organic \
            --challenges "$SMOKE_CHALLENGES" \
            --miner "miner=http://127.0.0.1:$PORT_A" \
            --miner "miner_naive=http://127.0.0.1:$PORT_B" 2>&1)
  code=$?
  echo "$SMOKE" | grep -E "uid[0-9]|weight =|friction at" || true
  # Any request failure means the signed path broke somewhere.
  if echo "$SMOKE" | grep -qE "rejected us|unreachable|Traceback"; then
    code=1
  fi
  record "validator scores both miners" "$code"
fi

echo
echo "================ SUMMARY ================"
for line in "${RESULTS[@]}"; do
  echo "  $line"
done
echo
echo "  $PASS passed, $FAIL failed"
[[ $FAIL -eq 0 ]] || exit 1
