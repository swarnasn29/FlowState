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
    python -m neurons.validator --once --miner miner=http://127.0.0.1:8091

Run two miners and compare them:
    python -m neurons.validator --once \
        --miner miner=http://127.0.0.1:8091 \
        --miner miner_naive=http://127.0.0.1:8092

Discover miners from the metagraph and write weights to chain:
    python -m neurons.validator --set-weights
"""

from __future__ import annotations

import argparse
import asyncio
import logging
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
from flowstate.scoring import (
    Scorecard,
    describe_rubric,
    score_response,
    timeout_scorecard,
)
from flowstate.telemetry import generate_organic, generate_task

log = logging.getLogger("flowstate.validator")

# Weight of the newest round's accuracy when folding it into a miner's running
# score, applied once per round and never per challenge. Low enough that one
# unlucky round cannot crater a good miner, high enough that a miner which
# stops working loses its weight within a few rounds.
SCORE_EMA_ALPHA = 0.2

# Rubric score a miner must beat before it earns anything. Set above every
# zero-effort strategy measured in flowstate.detect under the current rubric:
# a cold miner scores 0.524, "every non-ok ui_state" 0.439, "the slowest step"
# 0.370, and "claim nothing, ever" 0.280 (which is simply the fraction of
# sessions that are genuinely clean). None of those should be paid.
SCORE_FLOOR = 0.70

# Convexity of the incentive curve. Weight is (accuracy - floor) ** power.
#
# A linear curve is a broken incentive: a miner improving from 89% to 97% would
# earn roughly 8% more reward for substantially harder work, so nobody bothers.
# Squaring the excess over the floor means the better miner earns materially
# more, which is the whole mechanism by which a subnet gets better over time.
SCORE_POWER = 2.0


def shape_weight(accuracy: float, floor: float, power: float) -> float:
    """Turn a raw accuracy into an emission weight."""
    return max(0.0, accuracy - floor) ** power


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
    """Running record for one miner.

    `ema` is updated once per *round*, from that round's measured accuracy -
    never once per challenge. Folding single right/wrong answers in at alpha
    0.2 gives an effective memory of about five challenges, which turns the
    score into "did it get the last few right" instead of "how good is it",
    and hands out weights on luck. An earlier version did exactly that and
    scored a 90.8%-accurate miner at 0.66.
    """

    attempts: int = 0
    failures: int = 0
    perfect: int = 0
    ema: float = 0.0
    rounds: int = 0
    round_attempts: int = 0
    round_total: float = 0.0
    # Lifetime sums per criterion, so the report can say *why* a miner is
    # losing points rather than only that it is.
    sum_total: float = 0.0
    sum_localisation: float = 0.0
    sum_kind: float = 0.0
    sum_calibration: float = 0.0

    def _mean(self, total: float) -> float:
        return total / self.attempts if self.attempts else 0.0

    @property
    def mean_total(self) -> float:
        return self._mean(self.sum_total)

    @property
    def mean_localisation(self) -> float:
        return self._mean(self.sum_localisation)

    @property
    def mean_kind(self) -> float:
        return self._mean(self.sum_kind)

    @property
    def mean_calibration(self) -> float:
        return self._mean(self.sum_calibration)

    def record(self, card: Scorecard, timed_out: bool = False) -> None:
        """Note one marked session."""
        self.attempts += 1
        self.round_attempts += 1
        if timed_out:
            self.failures += 1
        if card.localisation == 1.0:
            self.perfect += 1
        self.round_total += card.total
        self.sum_total += card.total
        self.sum_localisation += card.localisation
        self.sum_kind += card.kind
        self.sum_calibration += card.calibration

    def end_round(self) -> float:
        """Fold this round's mean rubric score into the running score."""
        if not self.round_attempts:
            return self.ema
        round_accuracy = self.round_total / self.round_attempts
        if self.rounds == 0:
            # Nothing to blend with yet. Starting from 0.0 and moving 20% would
            # score a 95%-accurate miner at 0.19 and give it almost no weight
            # for no reason other than being new.
            self.ema = round_accuracy
        else:
            self.ema = (
                (1 - SCORE_EMA_ALPHA) * self.ema
                + SCORE_EMA_ALPHA * round_accuracy
            )
        self.rounds += 1
        self.round_attempts = 0
        self.round_total = 0.0
        return round_accuracy


async def discover_miners(
    client: bt.Subtensor,
    netuid: int,
    self_hotkey: str,
    overrides: list[tuple[str, str]] | None = None,
) -> list[MinerEndpoint]:
    """Find miners to query.

    Normally this reads published endpoints from the metagraph. Overrides exist
    because a miner on a laptop has no routable address to publish, so a local
    demo would otherwise be impossible without paying to register - and because
    running several miners on one host means several ports behind one IP.
    """
    metagraph = await client.subnets.metagraph(netuid=netuid)

    if overrides:
        endpoints = []
        for hotkey, url in overrides:
            neuron = metagraph.by_hotkey(hotkey)
            endpoint = MinerEndpoint(
                hotkey=hotkey,
                url=url.rstrip("/"),
                uid=neuron.uid if neuron is not None else None,
            )
            log.info("using override endpoint %s at %s", endpoint, endpoint.url)
            endpoints.append(endpoint)
        return endpoints

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
                # Latency is the pass/fail criterion: no answer in time is a
                # zero for the session, not a skipped session.
                score.record(timeout_scorecard(answer["findings"]), timed_out=True)
                continue
            wire = [finding.model_dump() for finding in reply.findings]
            score.record(score_response(wire, answer["findings"]))

    # Closing the round here rather than in the caller means the per-round
    # accuracy can never be left un-folded, which would freeze every weight.
    for endpoint in endpoints:
        scores.setdefault(endpoint.hotkey, MinerScore()).end_round()


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


def report(
    scores: dict[str, MinerScore],
    endpoints: list[MinerEndpoint],
    floor: float = SCORE_FLOOR,
    power: float = SCORE_POWER,
) -> None:
    """Print the standings, including the emission share each miner has earned.

    `share` is the number that matters: it is the proportion of this subnet's
    emission the miner would receive, after the incentive curve has turned a
    modest accuracy difference into a material reward difference.
    """
    by_hotkey = {endpoint.hotkey: endpoint for endpoint in endpoints}
    shaped = {
        hotkey: shape_weight(score.ema, floor, power)
        for hotkey, score in scores.items()
    }
    total = sum(shaped.values())

    print(f"\n  {'miner':<22}{'n':>5}{'SCORE':>8}{'loc':>6}{'kind':>6}"
          f"{'calib':>7}{'perfect':>9}{'weight':>9}{'share':>8}{'fails':>7}")
    for hotkey, score in sorted(scores.items(), key=lambda kv: -kv[1].ema):
        endpoint = by_hotkey.get(hotkey)
        label = str(endpoint) if endpoint else hotkey[:10] + ".."
        weight = shaped[hotkey]
        share = weight / total if total > 0 else 0.0
        perfect = score.perfect / score.attempts if score.attempts else 0.0
        print(
            f"  {label:<22}{score.attempts:>5}{score.mean_total:>8.3f}"
            f"{score.mean_localisation:>6.2f}{score.mean_kind:>6.2f}"
            f"{score.mean_calibration:>7.2f}{perfect:>9.1%}"
            f"{weight:>9.4f}{share:>8.1%}{score.failures:>7}"
        )
    print(f"  (weight = max(0, score - {floor:.2f}) ** {power:g};"
          f" 'perfect' = sessions localised exactly)")


async def submit_weights(
    client: bt.Subtensor,
    wallet: bt.Wallet,
    netuid: int,
    endpoints: list[MinerEndpoint],
    scores: dict[str, MinerScore],
    floor: float = SCORE_FLOOR,
    power: float = SCORE_POWER,
) -> None:
    """Write the current standings to chain.

    Only registered miners can receive weight. SetWeights conforms the values
    to the subnet's hyperparameters itself, so unnormalised numbers are fine -
    only the proportions matter, which is exactly what the shaping sets.
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
        weights.append(shape_weight(score.ema, floor, power))

    if not uids:
        log.warning("no registered miners to weight; skipping")
        return
    if sum(weights) <= 0:
        log.warning(
            "every miner is at or below the %.0f%% floor; skipping rather than "
            "committing a meaningless all-zero vector", floor * 100
        )
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


def resolve_hotkey(wallet_name: str, token: str) -> str:
    """Accept either a raw ss58 address or the name of a local wallet hotkey."""
    if token.startswith("5") and len(token) > 40:
        return token
    return bt.Wallet(name=wallet_name, hotkey=token).hotkey.ss58_address


async def run(args: argparse.Namespace) -> None:
    wallet = bt.Wallet(name=args.wallet, hotkey=args.hotkey)
    self_hotkey = wallet.hotkey.ss58_address
    log.info("validator hotkey %s", self_hotkey)
    # Printed every run on purpose: a scoring rule miners cannot read is a
    # scoring rule they reverse-engineer by trial and error.
    print()
    print(describe_rubric())

    overrides: list[tuple[str, str]] = []
    for spec in args.miner or ():
        if "=" not in spec:
            raise SystemExit(
                f"--miner expects HOTKEY=URL, got {spec!r}\n"
                f"  e.g. --miner miner=http://127.0.0.1:8091"
            )
        token, url = spec.split("=", 1)
        overrides.append((resolve_hotkey(args.wallet, token), url))

    scores: dict[str, MinerScore] = {}

    async with bt.Subtensor(args.network) as client:
        endpoints = await discover_miners(
            client, args.netuid, self_hotkey, overrides
        )
        if not endpoints:
            raise SystemExit(
                "no miners to query. Either run a miner that publishes its "
                "axon, or name them explicitly:\n"
                "  --miner miner=http://127.0.0.1:8091 "
                "--miner miner_naive=http://127.0.0.1:8092"
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
            report(scores, endpoints, args.score_floor, args.score_power)

            if args.organic:
                print("\n  --- organic stream (unscored) ---")
                for endpoint, reply in await organic_round(
                    wallet, endpoints, seed=900_000 + round_number
                ):
                    if reply is None:
                        print(f"  {endpoint}: no report")
                    elif not reply.findings:
                        print(f"  {endpoint}: no friction found  [not scored]")
                    else:
                        detail = ", ".join(
                            f"step {f.index} ({f.kind}) at {f.confidence:.2f}"
                            for f in reply.findings
                        )
                        print(f"  {endpoint}: {detail}  [not scored]")

            if args.set_weights:
                await submit_weights(
                    client, wallet, args.netuid, endpoints, scores,
                    args.score_floor, args.score_power,
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
    parser.add_argument(
        "--miner",
        action="append",
        metavar="HOTKEY=URL",
        help="query this miner instead of discovering from chain; repeatable. "
             "HOTKEY is a local wallet hotkey name or an ss58 address.",
    )
    parser.add_argument("--score-floor", type=float, default=SCORE_FLOOR,
                        help="accuracy a miner must beat to earn any weight")
    parser.add_argument("--score-power", type=float, default=SCORE_POWER,
                        help="convexity of the incentive curve")
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
