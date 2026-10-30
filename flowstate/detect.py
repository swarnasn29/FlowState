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

import math
import statistics
from dataclasses import dataclass, field

# How each signal converts into a comparable score. Desync dominates because
# a UI/chain disagreement has no benign explanation, while the behavioural
# signals all have innocent versions whose ranges overlap the friction ones.
SIGNAL_WEIGHTS: dict[str, float] = {
    "desync": 12.0,
    "stall": 1.2,
    "error_loop": 1.5,
    "rage_click": 0.9,
}

# Count signals are zero-inflated: most steps show no retries and no rage
# clicks at all. That breaks a quantile ceiling - when four fifths of samples
# are 0 the 80th percentile is 0, and one benign retry clears it. Surprisal
# measures how rare a value this high is for this step instead, in bits, which
# handles the zeros correctly. This floor ignores anything commoner than about
# one step in six.
SURPRISAL_FLOOR = 2.5

# Which friction kind each signal implies, when that signal is the strongest.
SIGNAL_TO_KIND: dict[str, str] = {
    "desync": "silent_failure",
    "stall": "stall",
    "error_loop": "error_loop",
    "rage_click": "rage_click",
}

# Robust z below this is ordinary variation in how long a step takes.
STALL_Z_FLOOR = 2.5

# How much lost time it takes before a human reads a wait as broken rather
# than slow. A relative anomaly alone is not enough: a step whose normal dwell
# is 800ms is five deviations slow at four seconds, and nobody abandons a swap
# over four seconds. Scaling the stall signal by absolute time lost stops the
# detector flagging short steps that merely wobbled, and stops a big z-score
# from drowning out the desync signal and mislabelling the friction.
#
# This is a UX judgement about user patience, not a fact about the generator.
USER_PATIENCE_MS = 10_000.0

# Weighted score an event must reach before this miner is willing to call it
# broken. This is the miner's own risk appetite, not knowledge of the
# generator: most sessions are clean, so a detector that reports its best
# candidate regardless is wrong most of the time. Calibrated against the
# signal weights - a desync alone (6.0) clears it easily, a single mild count
# excess (1.5) does not.
DECISION_THRESHOLD = 2.5

# Self-imposed cap, below the protocol's MAX_FINDINGS. Reporting more than two
# frictions in an eight-step flow is a claim this detector is never confident
# enough to make.
MAX_REPORTED = 3


# Signals learned per step. Dwell is continuous and scored by robust z
# weighted by absolute time lost; the other two are zero-inflated counts,
# scored by surprisal because median/MAD and quantiles both degenerate on
# a distribution that is mostly zeros.
COUNT_FIELDS = ("retries", "rage_clicks")


@dataclass
class Finding:
    """The miner's answer, plus the reasoning behind it."""

    friction_index: int
    friction_kind: str
    score: float
    confidence: float               # margin over the runner-up
    signals: dict[str, float] = field(default_factory=dict)

    def to_wire(self) -> dict:
        """The subset actually sent back over the wire."""
        return {
            "index": self.friction_index,
            "kind": self.friction_kind,
            "confidence": round(self.confidence, 4),
        }


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
        self._surprisal_cache: dict[tuple[str, str, float], float] = {}

    def observe(self, challenge: dict) -> None:
        """Absorb a session. Contaminated events are fine and expected."""
        for event in challenge["events"]:
            per_field = self._samples.setdefault(event["step"], {})
            per_field.setdefault("dwell_ms", []).append(float(event["dwell_ms"]))
            for name in COUNT_FIELDS:
                per_field.setdefault(name, []).append(float(event.get(name, 0)))
        self._dwell_cache.clear()
        self._surprisal_cache.clear()

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

    def median_dwell(self, step: str) -> float:
        """This step's normal dwell, 0.0 until the profile is ready."""
        if not self.ready(step):
            return 0.0
        self.robust_z(step, 0.0)        # populates the cache
        return self._dwell_cache[step][0]

    def surprisal(self, step: str, name: str, value: float) -> float:
        """Bits of surprise at a count this high for this step, 0.0 if unknown.

        Measures the empirical upper tail rather than a quantile, because
        these fields are mostly zero and a quantile ceiling degenerates to 0.
        Laplace smoothing keeps a finite sample from ever claiming infinite
        surprise at a value it simply has not seen yet.
        """
        if not self.ready(step) or value <= 0:
            return 0.0
        key = (step, name, float(value))
        if key not in self._surprisal_cache:
            samples = self._samples[step][name]
            at_least = sum(1 for sample in samples if sample >= value)
            probability = (at_least + 1) / (len(samples) + 1)
            self._surprisal_cache[key] = -math.log2(probability)
        return self._surprisal_cache[key]

    def excess(self, step: str, name: str, value: float) -> float:
        """How far past normal a count sits, in bits above the surprisal floor."""
        return max(0.0, self.surprisal(step, name, value) - SURPRISAL_FLOOR)


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

    # Unusually long for this step AND long enough in absolute terms that a
    # user would notice. Either test alone produces false positives: relative
    # alone flags a short step that wobbled, absolute alone flags a chain wait
    # that was always going to be slow.
    z = profile.robust_z(step, event["dwell_ms"])
    if z > STALL_Z_FLOOR:
        lost_ms = event["dwell_ms"] - profile.median_dwell(step)
        attention = min(1.0, max(0.0, lost_ms) / USER_PATIENCE_MS)
        strength = (z - STALL_Z_FLOOR) * attention
        if strength > 0:
            signals["stall"] = strength

    retries = profile.excess(step, "retries", event.get("retries", 0))
    if retries > 0:
        signals["error_loop"] = retries

    rage = profile.excess(step, "rage_clicks", event.get("rage_clicks", 0))
    if rage > 0:
        signals["rage_click"] = rage

    return signals


def analyse(challenge: dict, profile: StepProfile | None = None) -> list[Finding]:
    """Report every step this session looks broken at - possibly none.

    There is deliberately no fallback guess. An earlier version always named
    its best candidate, because the task guaranteed exactly one friction per
    session. Now most sessions are clean, so naming something regardless is
    wrong more often than it is right, and the rubric charges for it. Silence
    is an answer here.

    Each finding is named after whichever single signal contributed most to
    its score, and carries its own confidence - a miner can be certain about
    one step and hesitant about another in the same session.
    """
    profile = profile if profile is not None else StepProfile()

    findings: list[Finding] = []
    for event in challenge["events"]:
        signals = _event_signals(event, profile)
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
                # Maps the score onto 0..1 through the threshold: exactly at
                # the decision boundary this is 0.5, and it approaches 1 as
                # the evidence piles up. Keeps confidence honest, which the
                # calibration criterion then rewards.
                confidence=total / (total + DECISION_THRESHOLD),
                signals=signals,
            )
        )

    findings.sort(key=lambda finding: finding.score, reverse=True)
    return findings[:MAX_REPORTED]


if __name__ == "__main__":
    # Evaluation only - the miner itself never touches the generator.
    from flowstate.scoring import describe_rubric, score_response
    from flowstate.telemetry import FRICTION_KINDS, generate_task

    CALIBRATION = range(0, 2_000)       # sessions the miner has already seen
    EVALUATION = range(50_000, 52_000)  # disjoint, so nothing is memorised

    cold = StepProfile()
    warm = StepProfile()
    for seed in CALIBRATION:
        challenge, _ = generate_task(seed=seed)
        warm.observe(challenge)

    def profiled(challenge, profile):
        return [finding.to_wire() for finding in analyse(challenge, profile)]

    def cheat_ui(challenge, _profile):
        """Name every event whose ui_state is not ok."""
        return [
            {"index": event["index"], "kind": "stall", "confidence": 0.8}
            for event in challenge["events"] if event["ui_state"] != "ok"
        ]

    def cheat_slowest(challenge, _profile):
        """Name the slowest step, always."""
        event = max(challenge["events"], key=lambda e: e["dwell_ms"])
        return [{"index": event["index"], "kind": "stall", "confidence": 0.8}]

    def always_silent(challenge, _profile):
        """Claim nothing, ever. Scores whatever fraction of sessions is clean."""
        return []

    def run(responder, profile=None, kind=None):
        tally = {"total": 0.0, "loc": 0.0, "kind": 0.0, "cal": 0.0, "exact": 0}
        for seed in EVALUATION:
            challenge, answer = generate_task(seed=seed, kind=kind)
            card = score_response(responder(challenge, profile), answer["findings"])
            tally["total"] += card.total
            tally["loc"] += card.localisation
            tally["kind"] += card.kind
            tally["cal"] += card.calibration
            tally["exact"] += card.localisation == 1.0
        n = len(EVALUATION)
        return (tally["total"] / n, tally["loc"] / n,
                tally["kind"] / n, tally["cal"] / n, tally["exact"] / n)

    print(describe_rubric())
    print(f"\ncalibrated on {len(CALIBRATION)} sessions, "
          f"evaluated on {len(EVALUATION)} unseen\n")

    rows = (
        ("per-step profile, calibrated", profiled, warm),
        ("per-step profile, cold", profiled, cold),
        ("cheat: every non-ok ui_state", cheat_ui, None),
        ("cheat: the slowest step", cheat_slowest, None),
        ("cheat: claim nothing, ever", always_silent, None),
    )
    print(f"{'strategy':<32}{'SCORE':>8}{'loc':>7}{'kind':>7}{'calib':>8}{'perfect':>9}")
    for label, responder, profile in rows:
        total, loc, kinds, cal, exact = run(responder, profile)
        print(f"{label:<32}{total:>8.3f}{loc:>7.2f}{kinds:>7.2f}"
              f"{cal:>8.2f}{exact:>9.1%}")

    print(f"\n{'friction kind (forced present)':<32}{'SCORE':>8}{'loc':>7}{'kind':>7}")
    for friction in FRICTION_KINDS:
        total, loc, kinds, _, _ = run(profiled, warm, kind=friction)
        print(f"{friction:<32}{total:>8.3f}{loc:>7.2f}{kinds:>7.2f}")
