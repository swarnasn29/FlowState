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

Healthy dwell ranges are **per step**, and they differ by more than two orders
of magnitude: `quote_received` is normal at 300–1,200ms, while `tx_pending`
under chain congestion is normal all the way to 45,000ms — longer than an
injected stall's lower bound. This is the single most important property of the
data. It means **"the slowest step is the problem" is wrong**, and measurably
so: that heuristic scores 52.3%, worse than a miner with no history at all.

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

| Strategy | Accuracy |
|---|---|
| **Per-step profile, calibrated** | **97.0%** |
| Naive global thresholds | 89.5% |
| Per-step profile, cold — same code, no history | 62.0% |
| Cheat: "first non-ok `ui_state`" | 55.8% |
| Cheat: "the slowest step" | 52.3% |
| Random floor | 12.5% |

**The 35-point gap between calibrated and cold is the result that matters.** It
is the evidence that the per-step statistical model earns its place rather than
being decoration — same code, same thresholds, the only difference is
accumulated history. Both cheat baselines are *measured on every run*, not
quoted, so any change that re-leaks a label shows up immediately.

Note that both cheats now score *below* a cold miner. A single global "too
slow" threshold cannot work once a healthy `tx_pending` runs to 45 seconds,
which is the whole argument for modelling friction per step.

By friction kind: `silent_failure` 99.4%, `error_loop` 98.5%, `stall` 95.8%,
`rage_click` 94.5%. Rage-click is hardest because benign impatience reaches 6
clicks while injected rage starts at 4 — some cases are genuinely undecidable,
and a detector scoring 100% there would mean a label had leaked.

A live round reports a few points lower, because a fresh miner starts cold and
crosses its 30-session threshold partway through. That warm-up is a real
property of the design, worth showing rather than hiding.

### Does the subnet actually rank anyone?

The question a Protocol Track judge asks. `scripts/demo_compare.sh` runs the
real detector against a deliberately weaker one — `flowstate/detect_naive.py`,
which knows the desync trick and uses identical signal weights, and differs in
exactly one respect: global thresholds instead of per-step ones. Both miners
are genuinely trying.

400 scored challenges each, both registered on netuid 600:

| Miner | uid | Accuracy | Weight | Emission share |
|---|---|---|---|---|
| Per-step profile | 1 | 94.8% | 0.0613 | **58.7%** |
| Naive global | 2 | 90.8% | 0.0431 | **41.3%** |

Weight is **not** linear in accuracy:

```
weight = max(0, accuracy − 0.70) ²
```

Two deliberate choices. The **0.70 floor** sits above every zero-effort
strategy in the table above — a cold miner, both cheats and random all earn
nothing, which is a statement about what this subnet is willing to pay for.
**Squaring** turns a 4-point accuracy difference into a 1.4× reward difference,
because a linear curve is a broken incentive: a miner doing materially better
work would earn 4% more and nobody would bother. Both are tunable via
`--score-floor` and `--score-power`.

### Warm-up: the comparison inverts if the round is too short

A learning miner is worse than a hardcoded one until it has learned. Streaming
challenges the way a live round does:

| Challenges served | Per-step profile | Naive global | Leader |
|---|---|---|---|
| 40 | 62.5% | 80.0% | **naive** |
| 100 | 83.0% | 88.0% | **naive** |
| 200 | 89.0% | 84.5% | profile |
| 400 | 93.5% | 87.8% | profile |
| 1200 | 95.0% | 87.8% | profile |

Below roughly 200 challenges the better miner **loses**, because it is still
estimating each step's norms while the naive miner's fixed thresholds work from
the first request. `scripts/demo_compare.sh` therefore refuses to run with
fewer than 200 challenges rather than produce a backwards result.

This is a real property of the design, not a bug to hide: a subnet that rewards
learned behaviour has to tolerate new miners being temporarily bad, and that is
an argument for scoring over a long window rather than a single round.

---

## 5. On chain

| | |
|---|---|
| Network | `test.finney` |
| Subnet | **netuid 600**, published on chain as **FlowState** |
| Owner | coldkey `flowstate` |
| Validator | hotkey `validator`, **uid 0**, `validator_permit: true` |
| Miner (per-step profile) | hotkey `miner`, **uid 1** |
| Miner (naive, for comparison) | hotkey `miner_naive`, **uid 2** |
| Status | Activated (`start_call`), tempo 360 |

Both miners publish their endpoints with `ServeAxon`, and the validator finds
them **by reading the metagraph** — nothing is hardcoded:

```
INFO flowstate.validator  discovered 2 miner(s) with published endpoints
INFO flowstate.validator  scored round 1 complete (400 challenges)
```

Scores are then committed on chain — extrinsic `8326307-0007`, event
`TimelockedWeightsCommitted`. Subnet 600 has commit-reveal enabled, so
`SetWeights` takes the timelock path automatically and the chain reveals at a
later drand round.

The subnet's on-chain identity is published too (`8326573-0006`), so
`btcli subnet show 600` reports it as **FlowState** with this repo's URL rather
than as an anonymous netuid.

Two operational constraints worth knowing before a live run:

- **Weights are rate limited to one commit per 100 blocks (~20 min)** per
  validator. A second attempt inside that window fails with the chain's own
  message saying how many blocks remain — so a rehearsal that commits weights
  can make the real run fail minutes later.
- **Published axons hold an IP, and WSL's IP changes across reboots.** After a
  restart the addresses on chain are stale and discovery follows them to a host
  that no longer exists. Re-publish with `btcli tx serve-axon`.

`python -m scripts.preflight` checks both, plus whether the miners are actually
listening, and asks the chain to validate a real `SetWeights` without
submitting it:

```
 validator
  [ok  ] validator permit       yes
  [ok  ] weights settable       yes - last set 316 blocks ago, limit 100
  [ok  ] SetWeights preflight   accepted (not submitted)
 miners
  [ok  ] uid 1                  axon 172.26.78.118:8091 - reachable
  [ok  ] uid 2                  axon 172.26.78.118:8092 - reachable
  VERDICT: ready
```

It distinguishes the two failure modes that look identical from the outside: an
axon whose IP no longer belongs to this host (stale, re-publish) from one whose
IP is right but has nothing listening (miner not started).

**Cost of the whole thing:**

| Item | TAO |
|---|---|
| Create subnet | 1.0 |
| Register `miner` hotkey | 0.0078 |
| Register `miner_naive` hotkey | 0.0051 |
| Fees (registrations, activate, weights, serve) | ~0.004 |
| **Remaining** | **~1.98** |

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
  telemetry.py     validator side: session generation + friction injection
  detect.py        miner side: per-step profiling + anomaly detection
  detect_naive.py  deliberately weaker detector, for the ranking comparison
  protocol.py      the wire contract that replaces Synapse
neurons/
  miner.py         FastAPI server, signature auth, metagraph policy, ServeAxon
  validator.py     scored + organic streams, round scoring, SetWeights
tests/
  test_auth_loop.py
scripts/
  demo_local.sh    full loop on localhost, no chain spend
  demo_chain.sh    full loop on chain: publish, discover, score, set weights
  demo_compare.sh  two miners, one validator: the ranking demonstration
  wait_for_axon.py
```

## 8. Running it

Everything runs inside WSL2 Ubuntu. **Bittensor does not support native
Windows** — the SDK requires WSL2, and mining and validating are unsupported on
Windows outright.

```bash
source ~/venvs/flowstate/bin/activate
cd /mnt/c/Users/swarn/Documents/FlowStateProject

bash scripts/healthcheck.sh                # 8 code checks - is the code sound?
python -m scripts.preflight                # chain checks - will a run work NOW?
python -m scripts.preflight --require-miners   # ...strict: miners must be up
bash scripts/demo_local.sh                 # localhost, no chain spend
bash scripts/demo_compare.sh               # two miners ranked, no chain spend
DISCOVER=1 bash scripts/demo_compare.sh    # ...found via the metagraph
SET_WEIGHTS=1 bash scripts/demo_compare.sh # ...and commit the split on chain
PUBLISH_IP=$(hostname -I | awk '{print $1}') \
  bash scripts/demo_chain.sh               # full on-chain loop, one miner

python -m tests.test_auth_loop             # 7 auth cases
python -m flowstate.detect                 # accuracy + cheat baselines
python -m flowstate.detect_naive           # profiled vs naive, per kind
python -m flowstate.telemetry              # sample sessions + leak check
```

Weights are rate limited to one commit per 100 blocks (~20 minutes) per
validator, so a second `SET_WEIGHTS=1` run shortly after the first will report
how many blocks to wait rather than committing.

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

**A convex incentive curve, not linear weight on accuracy.** See §4. Linear
weighting means a materially better miner earns marginally more, so nobody
improves and the subnet stagnates.

**Scores update once per round, from that round's accuracy — never per
challenge.** An early version folded single right/wrong answers into an EMA at
alpha 0.2, giving an effective memory of about five challenges. It scored a
90.8%-accurate miner at 0.66 and handed out a 100%/0% emission split on pure
luck. The comment said "per round" while the code ran per challenge, and the
on-chain weights were wrong until it was caught.

**`tx_pending`'s healthy range was widened from 15s to 45s** on the grounds
that a congested chain genuinely takes that long. Worth stating plainly: this
change also happens to favour per-step modelling, since before it a single
global threshold worked fine. The justification is realism and the same change
would be right without the comparison — but it was a deliberate call, not an
accident of the data.

---

## 10. Not done

1. **Consensus scoring** for the organic stream (see §1).
2. **A real dApp feeding the organic stream.** Today organic sessions are
   fabricated and their answers discarded, which proves the path but not the
   product.
3. **Miners are only ever scored, never dropped.** A miner below the floor
   earns nothing but keeps its uid indefinitely. A production subnet would
   prune persistently useless neurons.
4. **One flow only.** Everything assumes a token swap. A second flow (NFT
   mint, bridge) would test whether the per-step approach generalises, and
   `SWAP_FLOW` being a module-level constant is the main thing in the way.
