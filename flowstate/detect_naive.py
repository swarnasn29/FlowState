"""A deliberately weaker FlowState detector, for comparison on chain.

This is not a straw man. It is roughly what a competent engineer writes after
skimming the spec: it knows about the UI/chain desync signal, because that is
the headline feature, and it applies sensible-looking thresholds to the
behavioural signals.

What it does not do is the one thing that actually matters - treat "slow" as a
per-step notion. It compares every step against the same global numbers, so a
`tx_pending` that is legitimately waiting on the chain looks exactly as broken
as a `quote_received` that has hung, and a user who merely got distracted looks
broken too.

It also learns nothing from the traffic it serves. Its thresholds on day one
are its thresholds forever.

The point of running it alongside the real detector is to show FlowState's
weights separating two miners that are both genuinely trying. A subnet that
cannot do that is not ranking anything.
"""

from __future__ import annotations

from flowstate.detect import Finding

# Fixed thresholds, applied identically to every step. Each is defensible in
# isolation and wrong in aggregate, which is exactly the failure being
# demonstrated: 20s is a long time to wait for a dropdown, and nothing at all
# to wait for a block.
GLOBAL_DWELL_MS = 20_000
GLOBAL_RETRIES = 2
GLOBAL_RAGE_CLICKS = 5

# Same relative ordering as the real detector, so the comparison isolates the
# per-step modelling rather than a difference in how signals are ranked.
SIGNAL_WEIGHTS: dict[str, float] = {
    "desync": 12.0,
    "stall": 1.2,
    "error_loop": 1.5,
    "rage_click": 0.9,
}

SIGNAL_TO_KIND: dict[str, str] = {
    "desync": "silent_failure",
    "stall": "stall",
    "error_loop": "error_loop",
    "rage_click": "rage_click",
}


class NaiveProfile:
    """Learns nothing.

    Exists only so that `neurons/miner.py` can swap detectors without caring
    which one it has: this satisfies the same `observe` interface and throws
    every observation away.
    """

    def observe(self, challenge: dict) -> None:
        return None


def _event_signals(event: dict) -> dict[str, float]:
    signals: dict[str, float] = {}

    chain = event.get("chain") or {}
    if event.get("ui_state") == "pending" and chain.get("status") == "confirmed":
        signals["desync"] = 1.0

    # Absolute slowness, with no idea what this step's normal looks like.
    if event["dwell_ms"] > GLOBAL_DWELL_MS:
        signals["stall"] = (event["dwell_ms"] - GLOBAL_DWELL_MS) / 10_000

    if event.get("retries", 0) > GLOBAL_RETRIES:
        signals["error_loop"] = float(event["retries"] - GLOBAL_RETRIES)

    if event.get("rage_clicks", 0) > GLOBAL_RAGE_CLICKS:
        signals["rage_click"] = float(event["rage_clicks"] - GLOBAL_RAGE_CLICKS)

    return signals


# The same decision threshold the real detector uses, so the comparison still
# isolates one idea. What this one lacks is a self-imposed cap: it reports
# everything that clears the bar, which on a clean session where a healthy
# tx_pending exceeds a fixed 20-second rule means inventing friction.
DECISION_THRESHOLD = 2.5


def analyse(challenge: dict, profile: NaiveProfile | None = None) -> list[Finding]:
    """Report every step that clears the global thresholds - possibly none.

    Signature matches `flowstate.detect.analyse`, so the two are
    interchangeable from the miner's point of view. `profile` is accepted and
    ignored, because this strategy learns nothing.
    """
    findings: list[Finding] = []
    for event in challenge["events"]:
        signals = _event_signals(event)
        if not signals:
            continue
        total = sum(SIGNAL_WEIGHTS[name] * value for name, value in signals.items())
        if total < DECISION_THRESHOLD:
            continue
        dominant = max(
            signals, key=lambda name: SIGNAL_WEIGHTS[name] * signals[name]
        )
        findings.append(
            Finding(
                friction_index=event["index"],
                friction_kind=SIGNAL_TO_KIND[dominant],
                score=round(total, 4),
                confidence=total / (total + DECISION_THRESHOLD),
                signals=signals,
            )
        )

    findings.sort(key=lambda finding: finding.score, reverse=True)
    return findings[:4]


if __name__ == "__main__":
    from flowstate.detect import StepProfile
    from flowstate.detect import analyse as analyse_profiled
    from flowstate.scoring import score_response
    from flowstate.telemetry import FRICTION_KINDS, generate_task

    CALIBRATION = range(0, 2_000)
    EVALUATION = range(50_000, 52_000)

    warm = StepProfile()
    for seed in CALIBRATION:
        challenge, _ = generate_task(seed=seed)
        warm.observe(challenge)

    def run(fn, profile, kind=None):
        total = loc = kinds = 0.0
        for seed in EVALUATION:
            challenge, answer = generate_task(seed=seed, kind=kind)
            wire = [f.to_wire() for f in fn(challenge, profile)]
            card = score_response(wire, answer["findings"])
            total += card.total
            loc += card.localisation
            kinds += card.kind
        n = len(EVALUATION)
        return total / n, loc / n, kinds / n

    print(f"evaluated on {len(EVALUATION)} unseen sessions\n")
    print(f"{'detector':<32}{'SCORE':>8}{'loc':>7}{'kind':>7}")
    for label, fn, profile in (
        ("per-step profile (calibrated)", analyse_profiled, warm),
        ("naive global thresholds", analyse, NaiveProfile()),
    ):
        total, loc, kinds = run(fn, profile)
        print(f"{label:<32}{total:>8.3f}{loc:>7.2f}{kinds:>7.2f}")

    print(f"\n{'friction kind':<32}{'profiled':>9}{'naive':>8}")
    for friction in FRICTION_KINDS:
        good, _, _ = run(analyse_profiled, warm, kind=friction)
        weak, _, _ = run(analyse, NaiveProfile(), kind=friction)
        print(f"{friction:<32}{good:>9.3f}{weak:>8.3f}")
