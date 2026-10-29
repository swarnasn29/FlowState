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

| Miner | Accuracy |
|---|---|
| Calibrated (learns per-step norms) | **97.7%** |
| Cold, same code, no history | **74.1%** |
| Cheat: "slowest step" | 73.7% |
| Cheat: "first non-ok ui_state" | 54.9% |
| Random floor | 12.5% |

The 23-point gap between calibrated and cold is the point: it is the evidence
that the per-step statistical model earns its place. Before this change cold
also scored 100%, which meant the model contributed nothing.
Per kind: stall 100%, silent_failure 99.7%, error_loop 98.4%,
rage_click 94.2% (hardest, because benign impatience reaches 6 and injected
rage starts at 4).

`flowstate/detect.py` measures both cheat baselines on every run rather than
quoting them, so a future change that re-leaks a label shows up immediately.

A live round shows ~90% rather than 97.7%, because a fresh miner starts cold
and crosses its 30-session threshold partway through. That warm-up is worth
demonstrating, not hiding.

## What is left

1. **A second, deliberately weaker miner**, to show the subnet actually
   separating a good miner from a bad one in the weights. The validator
   already handles N miners — `discover_miners` returns a list, challenges
   fan out concurrently, scores are per-hotkey and weights per-uid — but it
   has never run with more than one. Needs: a weak detector variant, a third
   hotkey registered (~τ0.008), and `--miner-url` extended to accept several
   URLs.
2. Nothing is committed to git — files are staged only.

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
