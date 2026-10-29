"""Friction detection for FlowState miners.

Given a session log, find the one step where friction was injected.

The miner never sees the validator's generator. It has to work out what
"normal" looks like from the traffic it receives, which is possible because
only one event per session is corrupted: roughly seven eighths of everything
it observes is healthy. Median and MAD tolerate that level of contamination
without needing clean training data, so the profile can be built straight
from live challenges.

Nothing here imports the validator's telemetry module except the evaluation
block at the bottom. Sharing the generator's distributions with the detector
would make any accuracy number we measured meaningless - and an earlier
version of this file did exactly that by accident, hardcoding the generator's
benign ceilings as constants and scoring a meaningless 100%.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

# How each signal converts into a comparable score. Desync dominates because
# a UI/chain disagreement has no benign explanation, while the behavioural
# signals all have innocent versions whose ranges overlap the friction ones.
SIGNAL_WEIGHTS: dict[str, float] = {
    "desync": 6.0,
    "stall": 1.2,
    "error_loop": 1.5,
    "rage_click": 0.9,
}

# Which friction kind each signal implies, when that signal is the strongest.
SIGNAL_TO_KIND: dict[str, str] = {
    "desync": "silent_failure",
    "stall": "stall",
    "error_loop": "error_loop",
    "rage_click": "rage_click",
}

# Robust z below this is ordinary variation in how long a step takes.
STALL_Z_FLOOR = 2.5

# The quantile of observed values treated as the top of normal for a step.
#
# Deliberately well below a conventional 0.95 or 0.99. Exactly one event per
# session is corrupted, so for an eight-step flow roughly an eighth of the
# samples recorded against any given step are friction. A ceiling at the 98th
# percentile would sit *inside* that corrupted tail and quietly normalise the
# very behaviour we are hunting for. 0.80 stays beneath the contamination.
#
# This follows from the shape of the task - one friction per session - and not
# from any knowledge of the generator's actual numbers.
BENIGN_QUANTILE = 0.80

# Signals learned per step. Dwell is continuous and scored by robust z; the
# other two are zero-inflated counts where median/MAD degenerates (median 0,
# MAD 0), so they are scored as excess over a learned ceiling instead.
COUNT_FIELDS = ("retries", "rage_clicks")


@dataclass
class Finding:
    """The miner's answer, plus the reasoning behind it."""

    friction_index: int
    friction_kind: str
    score: float
    confidence: float               # margin over the runner-up
    signals: dict[str, float] = field(default_factory=dict)

    def to_response(self) -> dict:
        """The subset actually sent back over the wire."""
        return {
            "friction_index": self.friction_index,
            "friction_kind": self.friction_kind,
            "confidence": round(self.confidence, 4),
        }


def _quantile(sorted_samples: list[float], q: float) -> float:
    """Nearest-rank quantile. No interpolation; these are small integer counts."""
    if not sorted_samples:
        return 0.0
    index = int(q * (len(sorted_samples) - 1) + 0.5)
    return float(sorted_samples[min(index, len(sorted_samples) - 1)])


class StepProfile:
    """Per-step behavioural baseline learned from observed sessions.

    Keyed by step name rather than index, because what counts as a slow
    `tx_pending` has nothing to do with what counts as a slow `select_pair`.
    This is the whole reason a global "slowest step is the problem" rule
    fails on this data.
    """

    def __init__(self, min_samples: int = 30) -> None:
        self.min_samples = min_samples
        # step -> field -> observed values
        self._samples: dict[str, dict[str, list[float]]] = {}
        self._dwell_cache: dict[str, tuple[float, float]] = {}
        self._ceiling_cache: dict[tuple[str, str], float] = {}

    def observe(self, challenge: dict) -> None:
        """Absorb a session. Contaminated events are fine and expected."""
        for event in challenge["events"]:
            per_field = self._samples.setdefault(event["step"], {})
            per_field.setdefault("dwell_ms", []).append(float(event["dwell_ms"]))
            for name in COUNT_FIELDS:
                per_field.setdefault(name, []).append(float(event.get(name, 0)))
        self._dwell_cache.clear()
        self._ceiling_cache.clear()

    def ready(self, step: str) -> bool:
        per_field = self._samples.get(step)
        if not per_field:
            return False
        return len(per_field.get("dwell_ms", ())) >= self.min_samples

    def robust_z(self, step: str, dwell_ms: float) -> float:
        """How many robust deviations above normal this dwell sits.

        Returns 0.0 until enough samples exist, so a cold miner leans on the
        signals that need no history rather than guessing from noise.
        """
        if not self.ready(step):
            return 0.0
        if step not in self._dwell_cache:
            samples = self._samples[step]["dwell_ms"]
            median = statistics.median(samples)
            mad = statistics.median([abs(x - median) for x in samples])
            # 1.4826 puts MAD on the same footing as a standard deviation.
            self._dwell_cache[step] = (median, 1.4826 * mad)
        median, scale = self._dwell_cache[step]
        if scale <= 0:
            return 0.0
        return (dwell_ms - median) / scale

    def ceiling(self, step: str, name: str) -> float:
        """The learned top of normal for a count signal on this step."""
        key = (step, name)
        if key not in self._ceiling_cache:
            samples = sorted(self._samples[step][name])
            self._ceiling_cache[key] = _quantile(samples, BENIGN_QUANTILE)
        return self._ceiling_cache[key]

    def excess(self, step: str, name: str, value: float) -> float:
        """How far past normal this count sits, 0.0 if within normal or unknown."""
        if not self.ready(step):
            return 0.0
        return max(0.0, value - self.ceiling(step, name))


def _event_signals(event: dict, profile: StepProfile) -> dict[str, float]:
    """Score one event on each signal independently.

    Independence matters: `error_loop` also drags dwell upward and adds a few
    rage clicks, so if the signals were entangled a single friction would
    triple-count and drown out a cleaner candidate elsewhere.
    """
    signals: dict[str, float] = {}
    step = event["step"]

    # The chain settled but the interface is still spinning. No healthy
    # session produces this - a genuinely pending transaction reports
    # `pending` on both sides, and a failed one reports `reverted` on both.
    chain = event.get("chain") or {}
    if event.get("ui_state") == "pending" and chain.get("status") == "confirmed":
        signals["desync"] = 1.0

    # Unusually long for this particular step, not in absolute terms.
    z = profile.robust_z(step, event["dwell_ms"])
    if z > STALL_Z_FLOOR:
        signals["stall"] = z - STALL_Z_FLOOR

    retries = profile.excess(step, "retries", event.get("retries", 0))
    if retries > 0:
        signals["error_loop"] = retries

    rage = profile.excess(step, "rage_clicks", event.get("rage_clicks", 0))
    if rage > 0:
        signals["rage_click"] = rage

    return signals


def detect(challenge: dict, profile: StepProfile | None = None) -> Finding:
    """Locate the friction point in one session.

    Picks the highest-scoring event, and names the friction after whichever
    single signal contributed most to that score.
    """
    profile = profile if profile is not None else StepProfile()
    events = challenge["events"]

    scored: list[tuple[float, int, dict[str, float]]] = []
    for event in events:
        signals = _event_signals(event, profile)
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
        # Nothing stood out at all. Still has to answer, so take the slowest
        # step and call it a stall - the least-wrong guess available.
        best_index = max(events, key=lambda e: e["dwell_ms"])["index"]
        kind = "stall"

    # Confidence is the gap to the next candidate, normalised. A session with
    # two plausible anomalies reports low confidence even when correct, which
    # lets the validator weight by calibration and not just hits.
    confidence = 0.0 if best_score <= 0 else (best_score - runner_up) / best_score

    return Finding(
        friction_index=best_index,
        friction_kind=kind,
        score=round(best_score, 4),
        confidence=confidence,
        signals=best_signals,
    )


if __name__ == "__main__":
    # Evaluation only - the miner itself never touches the generator.
    from flowstate.telemetry import FRICTION_KINDS, SWAP_FLOW, generate_task

    CALIBRATION = range(0, 2_000)       # sessions the miner has already seen
    EVALUATION = range(50_000, 52_000)  # disjoint, so nothing is memorised

    cold = StepProfile()
    warm = StepProfile()
    for seed in CALIBRATION:
        challenge, _ = generate_task(seed=seed)
        warm.observe(challenge)

    def evaluate(profile: StepProfile, kind: str | None = None) -> tuple[float, float]:
        hits = 0
        confidences = []
        for seed in EVALUATION:
            challenge, answer = generate_task(seed=seed, kind=kind)
            found = detect(challenge, profile)
            hits += found.friction_index == answer["friction_index"]
            confidences.append(found.confidence)
        n = len(EVALUATION)
        return hits / n, sum(confidences) / n

    def cheat_baseline() -> float:
        """The lazy miner: answer "whichever event is not ui_state == ok".

        Measured rather than quoted, so it tracks any change to the generator.
        If this ever climbs back toward 1.0, a label has leaked again.
        """
        hits = 0
        for seed in EVALUATION:
            challenge, answer = generate_task(seed=seed)
            guess = next(
                (e["index"] for e in challenge["events"] if e["ui_state"] != "ok"),
                None,
            )
            hits += guess == answer["friction_index"]
        return hits / len(EVALUATION)

    def slowest_baseline() -> float:
        """The other lazy miner: answer "the slowest step"."""
        hits = 0
        for seed in EVALUATION:
            challenge, answer = generate_task(seed=seed)
            guess = max(challenge["events"], key=lambda e: e["dwell_ms"])["index"]
            hits += guess == answer["friction_index"]
        return hits / len(EVALUATION)

    print(f"calibrated on {len(CALIBRATION)} sessions, "
          f"evaluated on {len(EVALUATION)} unseen\n")

    cold_acc, cold_conf = evaluate(cold)
    warm_acc, warm_conf = evaluate(warm)
    print(f"{'detector':<28}{'accuracy':>10}{'mean conf':>12}")
    print(f"{'calibrated':<28}{warm_acc:>10.1%}{warm_conf:>12.2f}")
    print(f"{'cold (no history)':<28}{cold_acc:>10.1%}{cold_conf:>12.2f}")
    print(f"{'cheat: first ui != ok':<28}{cheat_baseline():>10.1%}{'-':>12}")
    print(f"{'cheat: slowest step':<28}{slowest_baseline():>10.1%}{'-':>12}")
    print(f"{'random floor':<28}{1 / len(SWAP_FLOW):>10.1%}{'-':>12}")

    print(f"\n{'friction kind':<28}{'accuracy':>10}")
    for friction in FRICTION_KINDS:
        accuracy, _ = evaluate(warm, kind=friction)
        print(f"{friction:<28}{accuracy:>10.1%}")
