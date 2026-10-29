"""FlowState validator: scores miners and writes weights to chain.

Two streams run through the same miners over the same endpoint:

  * **scored** - the validator generates a session, keeps the injected index to
    itself, asks miners where the friction is, and marks the answers. This is
    the only thing weights are derived from, because it is the only thing that
    can be verified.
  * **organic** - a session from a real dApp, with no known answer. The report
    goes back to the caller and is never scored.

Miners cannot distinguish the two on the wire, which is what stops a miner
working hard only when it is being marked.

Run a single scored round against a local miner:
    python -m neurons.validator --once --miner-url http://127.0.0.1:8091

Run continuously and write weights to chain:
    python -m neurons.validator --set-weights
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
from dataclasses import dataclass, field

import bittensor as bt
import httpx

from flowstate.protocol import (
    DETECT_PATH,
    JSON_HEADERS,
    REQUEST_TIMEOUT_S,
    DetectRequest,
    DetectResponse,
    HotkeyAuth,
    canonical_body,
)
from flowstate.telemetry import generate_organic, generate_task

log = logging.getLogger("flowstate.validator")

# Weight of the newest round when folding it into a miner's running score. Low
# enough that one unlucky round cannot crater a good miner, high enough that a
# miner that stops working loses its weight within a few rounds.
SCORE_EMA_ALPHA = 0.2


@dataclass
class MinerEndpoint:
    """Where to reach one miner, and who to sign for.

    `uid` is None for a miner that is reachable but not registered on the
    subnet - useful for local testing. Such a miner can be queried and scored,
    but cannot receive weight, because weight is addressed by uid.
    """

    hotkey: str
    url: str
    uid: int | None = None

    def __str__(self) -> str:
        label = f"uid{self.uid}" if self.uid is not None else "unregistered"
        return f"{label}/{self.hotkey[:10]}.."


@dataclass
class MinerScore:
    """Running record for one miner."""

    attempts: int = 0
    correct: int = 0
    failures: int = 0
    ema: float = 0.0
    confidences: list[float] = field(default_factory=list)

    @property
    def accuracy(self) -> float:
        return self.correct / self.attempts if self.attempts else 0.0

    def record(self, correct: bool | None, confidence: float = 0.0) -> None:
        """Fold one answer in. `None` means the miner failed to answer."""
        self.attempts += 1
        if correct is None:
            self.failures += 1
            outcome = 0.0
        else:
            self.correct += correct
            outcome = float(correct)
            self.confidences.append(confidence)
        self.ema = (1 - SCORE_EMA_ALPHA) * self.ema + SCORE_EMA_ALPHA * outcome


async def discover_miners(
    client: bt.Subtensor,
    netuid: int,
    self_hotkey: str,
    override_url: str | None,
    override_hotkey: str | None,
) -> list[MinerEndpoint]:
    """Find miners to query.

    Normally this reads published endpoints from the metagraph. The override
    exists because a miner on a laptop has no routable address to publish, so
    a local demo would otherwise be impossible without paying to register.
    """
    metagraph = await client.subnets.metagraph(netuid=netuid)

    if override_url:
        assert override_hotkey is not None
        neuron = metagraph.by_hotkey(override_hotkey)
        endpoint = MinerEndpoint(
            hotkey=override_hotkey,
            url=override_url.rstrip("/"),
            uid=neuron.uid if neuron is not None else None,
        )
        log.info("using override endpoint %s at %s", endpoint, endpoint.url)
        return [endpoint]

    endpoints = []
    for neuron in metagraph.neurons:
        if neuron.hotkey == self_hotkey or not neuron.axon:
            continue
        endpoints.append(
            MinerEndpoint(
                hotkey=neuron.hotkey,
                url=f"http://{neuron.axon}",
                uid=neuron.uid,
            )
        )
    log.info("discovered %d miner(s) with published endpoints", len(endpoints))
    return endpoints


async def query_miner(
    wallet: bt.Wallet,
    endpoint: MinerEndpoint,
    challenge: dict,
) -> DetectResponse | None:
    """Ask one miner for its verdict. Returns None on any failure.

    Deliberately no retry. A retry would need a fresh signature - resending the
    same signed request is a replay and the miner will refuse it - and a miner
    too slow or broken to answer once has earned a zero for this round.
    """
    request = DetectRequest(**{
        key: challenge[key] for key in ("session_id", "flow", "steps", "events")
    })
    body = canonical_body(request)

    try:
        async with httpx.AsyncClient(
            base_url=endpoint.url,
            timeout=REQUEST_TIMEOUT_S,
            headers=JSON_HEADERS,
        ) as http:
            response = await http.post(
                DETECT_PATH,
                content=body,
                auth=HotkeyAuth(wallet, receiver_ss58=endpoint.hotkey),
            )
            response.raise_for_status()
            return DetectResponse(**response.json())
    except httpx.HTTPStatusError as exc:
        log.warning("%s rejected us: HTTP %d %s", endpoint,
                    exc.response.status_code, exc.response.text[:100])
    except (httpx.TransportError, ValueError) as exc:
        log.warning("%s unreachable or malformed: %s", endpoint, exc)
    return None


async def scored_round(
    wallet: bt.Wallet,
    endpoints: list[MinerEndpoint],
    scores: dict[str, MinerScore],
    challenges: int,
    seed_base: int,
) -> None:
    """Generate challenges, query every miner on each, and mark the answers."""
    for n in range(challenges):
        challenge, answer = generate_task(seed=seed_base + n)
        replies = await asyncio.gather(
            *(query_miner(wallet, endpoint, challenge) for endpoint in endpoints)
        )
        for endpoint, reply in zip(endpoints, replies):
            score = scores.setdefault(endpoint.hotkey, MinerScore())
            if reply is None:
                score.record(None)
                continue
            correct = reply.friction_index == answer["friction_index"]
            score.record(correct, reply.confidence)


async def organic_round(
    wallet: bt.Wallet,
    endpoints: list[MinerEndpoint],
    seed: int,
) -> list[tuple[MinerEndpoint, DetectResponse | None]]:
    """Relay one real-world session and return the reports, unscored.

    This is the product. Nothing here touches weights - there is no ground
    truth to score against, and inventing one would reward agreement with the
    validator's guess rather than correctness.
    """
    challenge = generate_organic(seed=seed)
    replies = await asyncio.gather(
        *(query_miner(wallet, endpoint, challenge) for endpoint in endpoints)
    )
    return list(zip(endpoints, replies))


def report(scores: dict[str, MinerScore], endpoints: list[MinerEndpoint]) -> None:
    by_hotkey = {endpoint.hotkey: endpoint for endpoint in endpoints}
    print(f"\n  {'miner':<26}{'n':>5}{'correct':>9}{'acc':>8}{'ema':>7}{'fails':>7}{'conf':>7}")
    for hotkey, score in sorted(scores.items(), key=lambda kv: -kv[1].ema):
        endpoint = by_hotkey.get(hotkey)
        label = str(endpoint) if endpoint else hotkey[:10] + ".."
        conf = statistics.mean(score.confidences) if score.confidences else 0.0
        print(
            f"  {label:<26}{score.attempts:>5}{score.correct:>9}"
            f"{score.accuracy:>8.1%}{score.ema:>7.2f}{score.failures:>7}{conf:>7.2f}"
        )


async def submit_weights(
    client: bt.Subtensor,
    wallet: bt.Wallet,
    netuid: int,
    endpoints: list[MinerEndpoint],
    scores: dict[str, MinerScore],
) -> None:
    """Write the current standings to chain.

    Only registered miners can receive weight. SetWeights conforms the values
    to the subnet's hyperparameters itself, so raw EMA scores are fine - only
    the proportions matter.
    """
    uids: list[int] = []
    weights: list[float] = []
    for endpoint in endpoints:
        if endpoint.uid is None:
            log.warning("%s is unregistered - cannot receive weight", endpoint)
            continue
        score = scores.get(endpoint.hotkey)
        if score is None:
            continue
        uids.append(endpoint.uid)
        weights.append(score.ema)

    if not uids:
        log.warning("no registered miners to weight; skipping")
        return
    if sum(weights) <= 0:
        log.warning("every score is zero; skipping to avoid a meaningless commit")
        return

    log.info("setting weights: %s", dict(zip(uids, (round(w, 3) for w in weights))))
    try:
        result = await client.execute(
            bt.SetWeights(netuid=netuid, uids=uids, weights=weights),
            wallet,
        )
        log.info("weights accepted: %s", result)
    except Exception as exc:
        # Rate limits and permit problems both land here; the chain's error
        # text says how many blocks to wait, so surface it rather than retry.
        log.error("SetWeights failed: %s: %s", type(exc).__name__, exc)


async def run(args: argparse.Namespace) -> None:
    wallet = bt.Wallet(name=args.wallet, hotkey=args.hotkey)
    self_hotkey = wallet.hotkey.ss58_address
    log.info("validator hotkey %s", self_hotkey)

    override_hotkey = None
    if args.miner_url:
        # Accept either a wallet hotkey name or a raw ss58 address.
        if args.miner_hotkey.startswith("5") and len(args.miner_hotkey) > 40:
            override_hotkey = args.miner_hotkey
        else:
            override_hotkey = bt.Wallet(
                name=args.wallet, hotkey=args.miner_hotkey
            ).hotkey.ss58_address

    scores: dict[str, MinerScore] = {}

    async with bt.Subtensor(args.network) as client:
        endpoints = await discover_miners(
            client, args.netuid, self_hotkey, args.miner_url, override_hotkey
        )
        if not endpoints:
            raise SystemExit(
                "no miners to query. Either run a miner that publishes its "
                "axon, or pass --miner-url http://127.0.0.1:8091 for a local "
                "demo."
            )

        seed_base = args.seed
        round_number = 0
        while True:
            round_number += 1
            await scored_round(
                wallet, endpoints, scores, args.challenges, seed_base
            )
            seed_base += args.challenges
            log.info("scored round %d complete (%d challenges)",
                     round_number, args.challenges)
            report(scores, endpoints)

            if args.organic:
                print("\n  --- organic stream (unscored) ---")
                for endpoint, reply in await organic_round(
                    wallet, endpoints, seed=900_000 + round_number
                ):
                    if reply is None:
                        print(f"  {endpoint}: no report")
                    else:
                        print(
                            f"  {endpoint}: friction at step index "
                            f"{reply.friction_index} ({reply.friction_kind}), "
                            f"confidence {reply.confidence:.2f}  [not scored]"
                        )

            if args.set_weights:
                await submit_weights(
                    client, wallet, args.netuid, endpoints, scores
                )

            if args.once:
                return
            await asyncio.sleep(args.interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="FlowState validator")
    parser.add_argument("--netuid", type=int, default=600)
    parser.add_argument("--network", default="test")
    parser.add_argument("--wallet", default="flowstate")
    parser.add_argument("--hotkey", default="validator")
    parser.add_argument("--challenges", type=int, default=20,
                        help="scored challenges per round")
    parser.add_argument("--interval", type=float, default=60.0,
                        help="seconds between rounds")
    parser.add_argument("--seed", type=int, default=0,
                        help="starting seed for generated challenges")
    parser.add_argument("--once", action="store_true",
                        help="run a single round and exit")
    parser.add_argument("--organic", action="store_true",
                        help="also relay one unscored organic session per round")
    parser.add_argument("--set-weights", action="store_true",
                        help="submit weights to chain after each round")
    parser.add_argument("--miner-url",
                        help="query this URL instead of discovering from chain")
    parser.add_argument("--miner-hotkey", default="miner",
                        help="wallet hotkey name or ss58 of the --miner-url miner")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    main()
