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
    "desync": 6.0,
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


def detect(challenge: dict, profile: NaiveProfile | None = None) -> Finding:
    """Locate the friction point using global thresholds only.

    Signature matches `flowstate.detect.detect` so the two are interchangeable
    from the miner's point of view. `profile` is accepted and ignored.
    """
    events = challenge["events"]

    scored: list[tuple[float, int, dict[str, float]]] = []
    for event in events:
        signals = _event_signals(event)
        total = sum(SIGNAL_WEIGHTS[name] * value for name, value in signals.items())
        scored.append((total, event["index"], signals))

    scored.sort(key=lambda row: row[0], reverse=True)
    best_score, best_index, best_signals = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0

    if best_signals:
        dominant = max(
            best_signals,
            key=lambda name: SIGNAL_WEIGHTS[name] * best_signals[name],
        )
        kind = SIGNAL_TO_KIND[dominant]
    else:
        best_index = max(events, key=lambda e: e["dwell_ms"])["index"]
        kind = "stall"

    confidence = 0.0 if best_score <= 0 else (best_score - runner_up) / best_score

    return Finding(
        friction_index=best_index,
        friction_kind=kind,
        score=round(best_score, 4),
        confidence=confidence,
        signals=best_signals,
    )


if __name__ == "__main__":
    from flowstate.detect import StepProfile
    from flowstate.detect import detect as detect_profiled
    from flowstate.telemetry import FRICTION_KINDS, generate_task

    CALIBRATION = range(0, 2_000)
    EVALUATION = range(50_000, 52_000)

    warm = StepProfile()
    for seed in CALIBRATION:
        challenge, _ = generate_task(seed=seed)
        warm.observe(challenge)

    def accuracy(fn, profile, kind: str | None = None) -> float:
        hits = 0
        for seed in EVALUATION:
            challenge, answer = generate_task(seed=seed, kind=kind)
            hits += fn(challenge, profile).friction_index == answer["friction_index"]
        return hits / len(EVALUATION)

    print(f"evaluated on {len(EVALUATION)} unseen sessions\n")
    print(f"{'detector':<30}{'accuracy':>10}")
    print(f"{'per-step profile (calibrated)':<30}{accuracy(detect_profiled, warm):>10.1%}")
    print(f"{'naive global thresholds':<30}{accuracy(detect, NaiveProfile()):>10.1%}")

    print(f"\n{'friction kind':<30}{'profiled':>10}{'naive':>8}")
    for friction in FRICTION_KINDS:
        good = accuracy(detect_profiled, warm, kind=friction)
        weak = accuracy(detect, NaiveProfile(), kind=friction)
        print(f"{friction:<30}{good:>10.1%}{weak:>8.1%}")
