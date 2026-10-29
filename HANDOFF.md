# FlowState — status as of 2026-10-29

## The loop works on chain

Subnet **600** on test.finney, owned by coldkey `flowstate`.
`validator` = uid 0, `miner` = uid 1. Subnet activated.

Verified end-to-end at 05:45 UTC: miner published its axon, the validator
discovered it from the metagraph, ran 20 signed challenges (20/20 correct),
and committed weights on chain — extrinsic `8325875-0007`,
`TimelockedWeightsCommitted`. The organic unscored stream also returns
reports. Balance remaining ~τ1.99.

## Running it

    source ~/venvs/flowstate/bin/activate
    cd /mnt/c/Users/swarn/Documents/FlowStateProject

    # no chain spend: miner + validator on localhost
    bash scripts/demo_local.sh

    # full on-chain: publishes axon, discovers from metagraph, sets weights
    PUBLISH_IP=$(hostname -I | awk '{print $1}') bash scripts/demo_chain.sh

    # the signed-request handshake, 7 cases, no chain needed
    python -m tests.test_auth_loop

    # generator samples + the label-leak regression check
    python -m flowstate.telemetry

    # detector accuracy, cold vs calibrated, per friction kind
    python -m flowstate.detect

## What is built

| File | Role |
|---|---|
| `flowstate/telemetry.py` | Validator-side session generator. Token-swap flow, 4 friction kinds, benign noise, `generate_organic` for the unscored stream |
| `flowstate/detect.py` | Miner-side detector. Per-step median/MAD profile + UI/chain desync signal |
| `flowstate/protocol.py` | The wire contract replacing `Synapse`: `DetectRequest`, `DetectResponse`, `canonical_body`, `HotkeyAuth`, `JSON_HEADERS` |
| `neurons/miner.py` | FastAPI server, hotkey-signature auth, metagraph caller policy, concurrent `ServeAxon` |
| `neurons/validator.py` | Scored + organic streams, EMA scoring, `SetWeights` |
| `tests/test_auth_loop.py` | Signature, receiver binding, tampering, replay, registration, permit |
| `scripts/` | `demo_local.sh`, `demo_chain.sh`, `wait_for_axon.py` |

## Difficulty, and why the numbers look like they do

Benign and friction ranges now deliberately **overlap**, so no threshold on
any single signal separates them: retries benign `1-3` vs friction `2-6`,
rage clicks benign `0-6` vs friction `4-15`, a distracted dwell up to 50s vs
a stall from 35s. The detector no longer hardcodes any ceiling — it learns
each step's top-of-normal as the 80th percentile of what it has observed,
chosen to sit *below* the ~1-in-8 contamination rate rather than inside it.

Measured over 2,000 unseen sessions after 2,000 calibration sessions:

| Strategy | Accuracy |
|---|---|
| Per-step profile, calibrated | **97.0%** |
| Naive global thresholds | 89.5% |
| Per-step profile, cold (no history) | 62.0% |
| Cheat: "first non-ok ui_state" | 55.8% |
| Cheat: "the slowest step" | 52.3% |
| Random floor | 12.5% |

The 35-point gap between calibrated and cold is the point: it is the evidence
that the per-step statistical model earns its place. Before the overlap change
cold also scored 100%, which meant the model contributed nothing.
Per kind: silent_failure 99.4%, error_loop 98.5%, stall 95.8%,
rage_click 94.5% (hardest, because benign impatience reaches 6 and injected
rage starts at 4).

Both cheats now score *below* a cold miner, because a single global "too slow"
threshold cannot survive `tx_pending` being legitimately slow (healthy up to
45s). `flowstate/detect.py` measures both baselines on every run rather than
quoting them, so a future change that re-leaks a label shows up immediately.

A live round shows a few points lower, because a fresh miner starts cold and
crosses its 30-session threshold partway through. That warm-up is worth
demonstrating, not hiding.

## Ranking two miners

`bash scripts/demo_compare.sh` runs the real detector against
`flowstate/detect_naive.py` (global thresholds, learns nothing, otherwise
identical). 400 challenges each, both registered on netuid 600:

| Miner | uid | Accuracy | Weight | Share |
|---|---|---|---|---|
| Per-step profile | 1 | 94.8% | 0.0613 | 58.7% |
| Naive global | 2 | 90.8% | 0.0431 | 41.3% |

Weight is `max(0, accuracy - 0.70) ** 2` — the floor sits above every
zero-effort strategy above, and squaring stops a linear curve from paying
4% more for substantially better work. Tunable with `--score-floor` and
`--score-power`.

## Pitch artifacts

Both are **private** Claude artifacts — nobody else can open them until shared
from each page's own Share menu.

- **Deck**, 12 slides, every one with speaker notes:
  https://claude.ai/artifact/CBvhDtwWu45oMbRL5eM3RH
  Arc: problem → mechanism → proof → limits. Downloads as .pptx or PDF.
- **One-page site**, for a submission link or a phone:
  https://claude.ai/artifact/T1gMK7X4bD47ELqM5xFUqn
  Same material, no JavaScript and no libraries, so nothing can fail to load.

Every figure in both comes from a script in this repo. If a number here
changes, re-run `python -m flowstate.detect` and `demo_compare.sh` and update
slides `skill`, `ranking` and `onchain`, plus the matching page sections.

## What is left

1. **Consensus scoring** for the organic stream — scoring unverifiable work
   means comparing miners against each other, which risks rewarding
   conformity over correctness.
2. **A real dApp feeding the organic stream.** Organic sessions are currently
   fabricated with their answers discarded, which proves the path, not the
   product.
3. **One flow only** — everything assumes a token swap. `SWAP_FLOW` being a
   module-level constant is the main thing in the way of a second flow.

## Traps already hit — do not re-learn these

- **Bittensor v11 removed axon/dendrite/Synapse entirely.** Every tutorial
  online is for v10 and will not work. You write your own FastAPI server and
  httpx client, signing with `bt.http_auth.sign` / `verify`.
- Metagraph is `await client.subnets.metagraph(netuid=600)`.
  `client.reads.subnets.metagraph` does **not** exist.
- **The chain will not store a loopback axon.** `ServeAxon(ip="127.0.0.1")`
  succeeds but records `ip=0`, making the miner invisible. Publish a routable
  address.
- **Never await `ServeAxon` before starting the server.** The serving rate
  limit can block it for minutes and hang miner startup. Run it concurrently.
- Weight setting needs only the uid from registration, not a published axon.
- Subnet 600 has commit-reveal on, so `SetWeights` takes the timelock path
  automatically and emits `TimelockedWeightsCommitted`.
- Raw request bodies must be hashed before parsing, and the **percent-encoded**
  path signed (`scope["raw_path"]`). Posting raw bytes without
  `content-type: application/json` gives a 422 that looks like an auth failure.
- A fresh signature is needed per attempt; transport-level retries are
  rejected as replays.
- `netuid` is positional for `btcli subnet show` / `burn-cost`, a `--netuid`
  flag for `subnet register`. btcli defaults to a hotkey named `default`.
- A new subnet's registration burn starts near the creation price and decays
  over hours: τ0.996 → τ0.0078 in about two hours. Wait before registering.
- Use `--dry-run` before every on-chain spend.
