"""The validator's scoring rubric.

A session used to be marked with one boolean: did the miner name the right
index. That cannot express most of what distinguishes a good miner from a
lucky one, so a response is now scored on four named criteria.

Pure functions, no dependencies — so the rubric can be tested on its own and
read by anyone who wants to know exactly how their miner is being judged.
That matters: a scoring rule miners cannot inspect is a scoring rule they
will optimise against by trial and error.
"""

from __future__ import annotations

from dataclasses import dataclass

# How the criteria combine. Localisation dominates because naming the broken
# step is the job; the rest decide between miners that can already do it.
WEIGHT_LOCALISATION = 0.70   # did it find the right steps, and only those
WEIGHT_KIND = 0.20           # did it name the right friction type
WEIGHT_CALIBRATION = 0.10    # was its confidence honest

# Latency is the fourth criterion and is deliberately pass/fail rather than
# graded: a miner that answers within REQUEST_TIMEOUT_S is judged on the three
# criteria above, and one that does not scores zero for the session. Grading
# speed on a curve would reward miners near the validator over better ones
# further away, which is a network-topology prize, not an accuracy prize.


@dataclass
class Scorecard:
    """A single session's marks, each criterion kept separately.

    Reported alongside the total on purpose. A miner losing points wants to
    know whether it is missing frictions, inventing them, mislabelling them,
    or simply overconfident - and those call for different fixes.
    """

    localisation: float     # F1 over the reported index set
    precision: float
    recall: float
    kind: float             # of the indices it got right, fraction labelled right
    calibration: float      # 1 - Brier score over reported confidences
    total: float
    reported: int
    truth: int
    hits: int

    @property
    def clean_session(self) -> bool:
        return self.truth == 0


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def score_response(findings: list[dict], truth: list[dict]) -> Scorecard:
    """Mark one response against the validator's hidden answer.

    `findings` is what the miner sent: dicts with `index`, `kind` and
    `confidence`. `truth` is the injected ground truth, which may be empty.

    An empty response to a clean session is a correct answer and scores 1.0.
    Staying silent when there was friction, and crying friction when there was
    none, both score 0 for localisation - a detector has to be willing to
    commit either way.
    """
    # Deduplicate by index: two findings on one step are one claim.
    reported: dict[int, dict] = {}
    for finding in findings:
        reported.setdefault(int(finding["index"]), finding)
    truth_kinds = {int(item["index"]): item["kind"] for item in truth}

    hit_indices = set(reported) & set(truth_kinds)

    if reported:
        precision = len(hit_indices) / len(reported)
    else:
        precision = 1.0 if not truth_kinds else 0.0

    if truth_kinds:
        recall = len(hit_indices) / len(truth_kinds)
    else:
        recall = 1.0 if not reported else 0.0

    localisation = _f1(precision, recall)

    # Kind is only meaningful for findings that landed on a real friction;
    # labelling a false positive correctly is not an achievement.
    if hit_indices:
        right_kind = sum(
            reported[i].get("kind") == truth_kinds[i] for i in hit_indices
        )
        kind = right_kind / len(hit_indices)
    elif not truth_kinds and not reported:
        kind = 1.0          # nothing to label, and it claimed nothing
    else:
        kind = 0.0

    # Brier score over what it actually claimed: a 0.9-confidence false
    # positive costs far more than a hedged one, and an unconfident hit is
    # only partly rewarded.
    if reported:
        squared = [
            (float(f.get("confidence", 0.0)) - (1.0 if i in hit_indices else 0.0)) ** 2
            for i, f in reported.items()
        ]
        calibration = 1.0 - sum(squared) / len(squared)
    else:
        # Silence is perfectly calibrated when the session was clean, and
        # maximally miscalibrated when it was not.
        calibration = 1.0 if not truth_kinds else 0.0

    total = (
        WEIGHT_LOCALISATION * localisation
        + WEIGHT_KIND * kind
        + WEIGHT_CALIBRATION * calibration
    )

    return Scorecard(
        localisation=localisation,
        precision=precision,
        recall=recall,
        kind=kind,
        calibration=calibration,
        total=total,
        reported=len(reported),
        truth=len(truth_kinds),
        hits=len(hit_indices),
    )


def timeout_scorecard(truth: list[dict]) -> Scorecard:
    """The mark for a miner that did not answer in time: zero on everything."""
    return Scorecard(
        localisation=0.0,
        precision=0.0,
        recall=0.0,
        kind=0.0,
        calibration=0.0,
        total=0.0,
        reported=0,
        truth=len(truth),
        hits=0,
    )


RUBRIC = (
    ("localisation", WEIGHT_LOCALISATION,
     "F1 over the reported step indices: finds the broken steps, and only those"),
    ("kind", WEIGHT_KIND,
     "of the steps it located, the fraction it labelled with the right friction type"),
    ("calibration", WEIGHT_CALIBRATION,
     "1 - Brier score over its own confidences: is a confident claim more often right"),
    ("latency", 0.0,
     "pass/fail - no answer inside the request timeout scores zero for the session"),
)


def describe_rubric() -> str:
    """The criteria, for printing where miners and judges can read them."""
    lines = ["scoring rubric (weights sum to 1.00 over the graded criteria):"]
    for name, weight, text in RUBRIC:
        label = f"{weight:.2f}" if weight else "gate"
        lines.append(f"  {label:>5}  {name:<13} {text}")
    return "\n".join(lines)


if __name__ == "__main__":
    print(describe_rubric())
    print()

    cases = (
        ("clean session, silent", [], []),
        ("clean session, one false positive",
         [{"index": 3, "kind": "stall", "confidence": 0.9}], []),
        ("clean session, hedged false positive",
         [{"index": 3, "kind": "stall", "confidence": 0.2}], []),
        ("one friction, found with right kind",
         [{"index": 4, "kind": "silent_failure", "confidence": 0.9}],
         [{"index": 4, "kind": "silent_failure"}]),
        ("one friction, found with wrong kind",
         [{"index": 4, "kind": "stall", "confidence": 0.9}],
         [{"index": 4, "kind": "silent_failure"}]),
        ("one friction, missed entirely",
         [], [{"index": 4, "kind": "silent_failure"}]),
        ("two frictions, both found",
         [{"index": 1, "kind": "stall", "confidence": 0.8},
          {"index": 6, "kind": "rage_click", "confidence": 0.7}],
         [{"index": 1, "kind": "stall"}, {"index": 6, "kind": "rage_click"}]),
        ("two frictions, one found",
         [{"index": 1, "kind": "stall", "confidence": 0.8}],
         [{"index": 1, "kind": "stall"}, {"index": 6, "kind": "rage_click"}]),
        ("two frictions, sprayed four guesses",
         [{"index": i, "kind": "stall", "confidence": 0.5} for i in (0, 1, 2, 6)],
         [{"index": 1, "kind": "stall"}, {"index": 6, "kind": "rage_click"}]),
    )

    header = f"{'case':<38}{'loc':>7}{'kind':>7}{'calib':>8}{'TOTAL':>8}"
    print(header)
    for label, findings, truth in cases:
        card = score_response(findings, truth)
        print(f"{label:<38}{card.localisation:>7.2f}{card.kind:>7.2f}"
              f"{card.calibration:>8.2f}{card.total:>8.2f}")
