# FlowState

**A Web3 Behavioral UX Analytics subnet on Bittensor.**
Live on `test.finney` as **netuid 600**.

dApps feed user telemetry — UI interactions plus wallet and chain events — into
the subnet. Miners locate the exact step where a user hit friction. Validators
score them on sessions where the answer is known, and pay out accordingly.

The premise: Web3 UX fails in ways Web2 analytics cannot see. A tool watching
only the interface sees a spinner; it cannot see that the transaction settled
forty seconds ago and the UI never found out. Detecting that requires reading
the interface and the chain side by side, which is what this subnet does.

---

## 1. Architecture: two streams, one miner

The hardest problem in this design is not detection. It is **scoring**.

Real telemetry has no answer key. A miner replies "friction at checkout,
confidence 0.8" and nobody knows whether it is right. That breaks a subnet in
three ways at once: a lazy miner returning a constant answer is
indistinguishable from a diligent one; two honest validators grade differently
and Bittensor's consensus penalises both for diverging; and once you score
"report quality" miners optimise for the proxy — longer reports, more
confident language — instead of correctness.

So FlowState runs two streams through the same miners, over the same endpoint:

| | **Scored stream** | **Organic stream** |
|---|---|---|
| Source | Validator generates the session | Real dApp sends a real session |
| Answer known | Yes — kept server-side | No |
| Purpose | Sets weights, pays miners | **The product**: the report goes back |
| Scored | Yes | Never |

The scored stream is not the product. It is the **exam for a licence**: the only
thing that can be verified, and therefore the only honest basis for ranking. The
organic stream is what the subnet is actually for.

Critically, **the two are byte-identical on the wire**. A miner that could tell
them apart would work hard only when marked. The distinction lives entirely on
the validator's side and never appears in a request.

> **Known limitation.** Scoring real traffic requires comparing miners against
> each other — if nine miners flag step 4 and one flags step 1, the outlier is
> probably wrong. That is consensus scoring, and it is deliberately out of scope
> here: done carelessly it rewards conformity over correctness.

---

## 2. The data

A session is one user attempting a **token swap**, the flow with the richest
friction surface in Web3 (two wallet popups, an approval, a slippage window,
a chain wait).

```
wallet_connect → select_pair → input_amount → quote_received
→ token_approve → confirm_swap → tx_pending → tx_success
```

Each event carries what the interface was showing and, on steps that touch the
chain, what the chain itself reported:

```json
{
  "index": 6,
  "step": "tx_pending",
  "t_ms": 17803,
  "dwell_ms": 24602,
  "ui_state": "pending",
  "retries": 0,
  "rage_clicks": 0,
  "errors": [],
  "chain": { "tx_hash": "0x…", "status": "confirmed" }
}
```

Healthy dwell ranges are **per step**, and the ranges differ by more than an
order of magnitude: `quote_received` is normal at 300–1,200ms while `tx_pending`
is normal at 3,000–15,000ms. This is deliberate. It means **"the slowest step is
the problem" is wrong**, and a miner that assumes it fails.

### Friction taxonomy

| Kind | What the user experiences | Signature |
|---|---|---|
| `stall` | A spinner that never resolves | Dwell far outside this step's norm |
| `error_loop` | The allowance / slippage trap, over and over | Repeated identical errors, retries |
| `rage_click` | The button looks dead, so they hammer it | Rage clicks far above this step's norm |
| `silent_failure` | **It worked. The UI never said so.** | `ui_state: pending` vs `chain: confirmed` |

`silent_failure` is the one that justifies putting this on a chain at all. There
is no error, no rage, nothing broken-looking — just a user waiting on a result
they already have. UI-only analytics is structurally blind to it. The only
evidence is the disagreement between the two sources.

---

## 3. Detection

Miners never see the generator. They work out what "normal" means from the
traffic they are served, which is possible because **only one event per session
is corrupted**: roughly seven eighths of everything a miner observes is healthy.
Median and MAD tolerate that contamination, so no clean training set is needed —
the validator's challenges *are* the training data.

Four signals, scored independently so one friction cannot triple-count:

- **desync** — `ui_state: pending` against `chain: confirmed`. Weighted highest
  because it has no benign explanation: a genuinely pending transaction reports
  `pending` on both sides, a failed one reports `reverted` on both.
- **stall** — robust z-score of dwell against this step's learned median/MAD.
- **error_loop** — retries above this step's learned ceiling.
- **rage_click** — rage clicks above this step's learned ceiling.

That ceiling is the **80th percentile** of observed values, not the conventional
95th or 99th. With one friction per session across an eight-step flow, about an
eighth of the samples recorded against any step *are* friction; a 98th-percentile
ceiling would sit inside that corrupted tail and quietly normalise the very
behaviour being hunted. 0.80 stays beneath it. That reasoning follows from the
shape of the task, not from knowledge of the generator.

A cold miner has no history, so the statistical signals return zero and it falls
back to desync plus a "slowest step" guess. It gets better as it serves traffic.

---

## 4. Results

Measured over 2,000 unseen sessions after 2,000 calibration sessions
(`python -m flowstate.detect`):

| Miner | Accuracy |
|---|---|
| **Calibrated** — learns per-step norms | **97.7%** |
| **Cold** — same code, no history | **74.1%** |
| Cheat: "the slowest step" | 73.7% |
| Cheat: "first non-ok `ui_state`" | 54.9% |
| Random floor | 12.5% |

**The 23-point gap between calibrated and cold is the result that matters.** It
is the evidence that the per-step statistical model earns its place rather than
being decoration. Both cheat baselines are *measured on every run*, not quoted,
so any change that re-leaks a label shows up immediately.

By friction kind: `stall` 100%, `silent_failure` 99.7%, `error_loop` 98.4%,
`rage_click` 94.2%. Rage-click is hardest because benign impatience reaches 6
clicks while injected rage starts at 4 — some cases are genuinely undecidable,
and a detector scoring 100% there would mean a label had leaked.

A live round reports ~90% rather than 97.7%, because a fresh miner starts cold
and crosses its 30-session threshold partway through. That warm-up is a real
property of the design, worth showing rather than hiding.

---

## 5. On chain

| | |
|---|---|
| Network | `test.finney` |
| Subnet | **netuid 600**, owned by coldkey `flowstate` |
| Validator | hotkey `validator`, **uid 0**, `validator_permit: true` |
| Miner | hotkey `miner`, **uid 1** |
| Status | Activated (`start_call`), tempo 360 |

Verified end-to-end: the miner published its axon with `ServeAxon`, the
validator discovered it **by reading the metagraph** (not a hardcoded URL), ran
signed challenges, and committed weights on chain —
extrinsic `8325875-0007`, event `TimelockedWeightsCommitted`. Subnet 600 has
commit-reveal enabled, so `SetWeights` takes the timelock path automatically.

**Cost of the whole thing:**

| Item | TAO |
|---|---|
| Create subnet | 1.0 |
| Register miner hotkey | 0.0078 |
| Fees (register, activate, weights, serve) | ~0.002 |
| **Remaining** | **~1.99** |

Registration cost τ0.0078 instead of τ0.99 because a new subnet's burn starts
near the creation price and decays over hours. Waiting about two hours before
registering saved nearly a full TAO.

---

## 6. Security

Bittensor v11 removed the `axon`/`dendrite`/`Synapse` stack, so the transport is
plain HTTP that we authenticate ourselves. Every request is signed with the
caller's hotkey and verified before any work is done.

`python -m tests.test_auth_loop` — **7/7 passing**, no chain required:

| Case | Expected |
|---|---|
| Correctly signed request | 200, well-formed answer |
| No signature | 401 |
| Signed for a different receiver | 401 |
| Body tampered after signing | 401 |
| Signature replayed | 401 on the second attempt |
| Caller not registered on the subnet | 403 |
| Caller without a validator permit | 403 |

Authentication and authorisation are separate steps on purpose: anyone can hold
a valid hotkey, but only a hotkey registered on this subnet should be able to
spend a miner's CPU.

---

## 7. Layout

```
flowstate/
  telemetry.py    validator side: session generation + friction injection
  detect.py       miner side: per-step profiling + anomaly detection
  protocol.py     the wire contract that replaces Synapse
neurons/
  miner.py        FastAPI server, signature auth, metagraph policy, ServeAxon
  validator.py    scored + organic streams, EMA scoring, SetWeights
tests/
  test_auth_loop.py
scripts/
  demo_local.sh   full loop on localhost, no chain spend
  demo_chain.sh   full loop on chain: publish, discover, score, set weights
  wait_for_axon.py
```

## 8. Running it

Everything runs inside WSL2 Ubuntu. **Bittensor does not support native
Windows** — the SDK requires WSL2, and mining and validating are unsupported on
Windows outright.

```bash
source ~/venvs/flowstate/bin/activate
cd /mnt/c/Users/swarn/Documents/FlowStateProject

bash scripts/demo_local.sh                 # localhost, no chain spend
PUBLISH_IP=$(hostname -I | awk '{print $1}') \
  bash scripts/demo_chain.sh               # full on-chain loop

python -m tests.test_auth_loop             # 7 auth cases
python -m flowstate.detect                 # accuracy + cheat baselines
python -m flowstate.telemetry              # sample sessions + leak check
```

---

## 9. Decisions worth defending

**Own subnet rather than joining one.** On an established subnet our miner
competes with mature ones for queries and may receive no traffic at all — a dead
demo. Owning netuid 600 costs τ1.0 and makes the loop deterministic.

**Synthetic scoring, despite it not being the product.** See §1. It is the only
verifiable signal, and an unverifiable one collapses the incentive.

**Chain data in the schema, not just UI events.** Without it `silent_failure` is
undetectable, and `silent_failure` is the strongest argument that this subnet
does something a Web2 analytics tool cannot.

**Benign and friction ranges overlap deliberately.** An earlier version had them
disjoint, and the detector scored 100% on every category — including with its
statistical model entirely disabled. Four `if` statements solved the task. A
subnet whose miners all score 100% cannot rank anyone, which is the one thing a
Bittensor subnet exists to do. Overlapping them forces a miner to weigh several
weak, ambiguous clues against what is normal for that step.

**Timestamps are recomputed after injection.** Injecting a 75-second stall
without re-stamping later events would leave timestamps contradicting their own
dwell times — detectable by arithmetic rather than behaviour, which would make
the subnet measure the wrong thing.

**The miner's HTTP server never waits on a chain call.** `ServeAxon` can block
for minutes under the chain's serving rate limit. A miner that answers nothing
while it waits is losing score for free, so publishing runs concurrently with
serving.

---

## 10. Not done

1. **A second, deliberately weaker miner**, to show the weights actually
   separating a good miner from a bad one. The validator already handles N
   miners — challenges fan out concurrently, scores are per-hotkey, weights per
   uid — but it has never run with more than one. Needs a weak detector variant,
   a third registered hotkey, and multi-URL support in the validator.
2. **Consensus scoring** for the organic stream (see §1).
3. **A real dApp feeding the organic stream.** Today organic sessions are
   fabricated and their answers discarded, which proves the path but not the
   product.
