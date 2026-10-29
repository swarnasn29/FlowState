"""Synthetic Web3 telemetry generation for FlowState validators.

A *session* is the event log of one user attempting a token swap. The validator
generates a healthy session, injects exactly one friction point at a known
index, and challenges miners to locate it. The injected index is the ground
truth and never leaves the validator.

Events carry both UI state and, on steps that touch the chain, the chain's own
view of the same moment. Keeping the two side by side is what lets the subnet
detect friction that UI-only analytics structurally cannot see.
"""

from __future__ import annotations

import json
import random
import uuid
from dataclasses import asdict, dataclass, field

# The token swap flow as a user actually experiences it. Each step carries the
# healthy dwell-time range in milliseconds: how long someone normally lingers
# before advancing. The wallet-popup and chain-wait steps are legitimately slow,
# which is exactly what makes naive "slowest step wins" detection fail.
SWAP_FLOW: list[tuple[str, int, int]] = [
    ("wallet_connect", 400, 1_800),
    ("select_pair", 800, 4_000),
    ("input_amount", 1_200, 6_000),
    ("quote_received", 300, 1_200),
    ("token_approve", 2_000, 9_000),   # MetaMask popup - inherently slow
    ("confirm_swap", 1_500, 6_000),    # second popup
    # Chain latency, and under congestion a perfectly healthy confirmation can
    # take the best part of a minute. This range overlaps the injected stall
    # range on purpose: it is what makes a single global "too slow" threshold
    # useless, because 40s is alarming here and unremarkable on the step above.
    ("tx_pending", 3_000, 45_000),
    ("tx_success", 400, 2_000),
]

# Steps where the dApp submits something and then waits on the chain. These are
# the only steps that can silently desync: the UI keeps spinning while the
# transaction has in fact already settled.
AWAIT_STEPS = ("token_approve", "confirm_swap", "tx_pending")

FRICTION_KINDS = ("stall", "error_loop", "rage_click", "silent_failure")

# Errors a swap UI realistically throws, keyed by the step they belong to.
_STEP_ERRORS: dict[str, list[str]] = {
    "wallet_connect": ["ERR_NO_PROVIDER", "ERR_USER_REJECTED"],
    "select_pair": ["ERR_PAIR_NOT_FOUND"],
    "input_amount": ["ERR_INSUFFICIENT_BALANCE"],
    "quote_received": ["ERR_QUOTE_EXPIRED", "ERR_NO_ROUTE"],
    "token_approve": ["ERR_INSUFFICIENT_ALLOWANCE", "ERR_USER_REJECTED"],
    "confirm_swap": ["ERR_SLIPPAGE_EXCEEDED", "ERR_GAS_ESTIMATION_FAILED"],
    "tx_pending": ["ERR_TX_UNDERPRICED", "ERR_NONCE_TOO_LOW"],
    "tx_success": [],
}


@dataclass
class Event:
    """One step in a user's journey through the swap.

    `ui_state` is what the interface was showing the user. `chain` is the
    chain's own view, present only on AWAIT_STEPS. When those two disagree,
    the user is staring at a lie.
    """

    index: int
    step: str
    t_ms: int           # milliseconds since session start
    dwell_ms: int       # time spent on this step before advancing
    ui_state: str = "ok"            # ok | pending | error
    retries: int = 0
    rage_clicks: int = 0
    errors: list[str] = field(default_factory=list)
    chain: dict | None = None       # {"tx_hash": ..., "status": ...}


def _tx_hash(rng: random.Random) -> str:
    digits = "0123456789abcdef"
    return "0x" + "".join(rng.choice(digits) for _ in range(64))


# How often benign, non-friction noise shows up in a healthy session. Without
# it the injected friction is the only irregular thing in the log, and "find
# the one event that isn't pristine" scores perfectly with no real analysis.
P_BENIGN_PENDING = 0.35      # an await step still settling - UI and chain agree
P_BENIGN_ERROR = 0.35        # a few rejected popups, then the user carries on
P_BENIGN_DISTRACTED = 0.50   # the user looked away during one step


def _healthy_events(rng: random.Random) -> list[Event]:
    """Build a run through the flow with no friction, but realistic noise.

    "Healthy" is not the same as pristine. Real sessions contain pending
    spinners, the occasional rejected wallet popup, and users who wander off
    mid-flow. All of it is benign, and all of it has to be here - otherwise
    the friction injected afterwards stands out by being the only blemish.
    """
    events: list[Event] = []
    for index, (step, low, high) in enumerate(SWAP_FLOW):
        chain = None
        ui_state = "ok"
        if step in AWAIT_STEPS:
            if rng.random() < P_BENIGN_PENDING:
                # Still confirming. Slow, but the UI is telling the truth.
                chain = {"tx_hash": _tx_hash(rng), "status": "pending"}
                ui_state = "pending"
            else:
                chain = {"tx_hash": _tx_hash(rng), "status": "confirmed"}
        events.append(
            Event(
                index=index,
                step=step,
                t_ms=0,  # filled in by _recompute_timeline
                dwell_ms=rng.randint(low, high),
                ui_state=ui_state,
                # Impatience is normal. This reaches 6, overlapping the
                # injected rage_click range, so the raw count proves nothing on
                # its own - only how it compares to this step's own norm does.
                rage_clicks=rng.choices(
                    (0, 1, 2, 3, 4, 5, 6),
                    weights=(40, 25, 15, 8, 5, 4, 3),
                )[0],
                chain=chain,
            )
        )
    _add_benign_noise(events, rng)
    _recompute_timeline(events)
    return events


def _add_benign_noise(events: list[Event], rng: random.Random) -> None:
    """Scatter harmless irregularities whose ranges overlap real friction.

    The overlap is the point. Benign retries reach 3 while injected ones start
    at 2; benign rage clicks reach 6 while injected ones start at 4; a
    distracted user reaches 50s while a stall starts at 35s. No threshold on
    any single signal separates the two populations, so a miner has to weigh
    several weak, ambiguous clues against what is normal *for that step*
    instead of pattern-matching one obvious tell.
    """
    # A rejected wallet popup or two that the user simply tried again.
    if rng.random() < P_BENIGN_ERROR:
        candidates = [event for event in events if _STEP_ERRORS.get(event.step)]
        if candidates:
            event = rng.choice(candidates)
            event.retries = rng.randint(1, 3)
            event.errors = [rng.choice(_STEP_ERRORS[event.step])] * event.retries
            event.ui_state = "error"
            if event.chain is not None:
                # Keep the chain consistent with the UI: the submission really
                # did fail. A benign error must never look like a desync, or
                # it would be indistinguishable from silent_failure.
                event.chain["status"] = "reverted"

    # Someone switched tabs during one step. Slower than usual, nothing wrong.
    if rng.random() < P_BENIGN_DISTRACTED:
        event = rng.choice(events)
        # Reaches into stall territory on purpose. A long dwell is now genuinely
        # ambiguous, so duration alone cannot settle whether a step is broken.
        stretched = int(event.dwell_ms * rng.uniform(1.5, 3.5))
        event.dwell_ms = min(stretched, 50_000)


def _recompute_timeline(events: list[Event]) -> None:
    """Re-stamp t_ms so timestamps stay consistent after dwell times change.

    Without this, injecting a 60-second stall would leave the later timestamps
    contradicting their own dwell times - a tell that has nothing to do with
    the behavior we want miners to learn.
    """
    clock = 0
    for event in events:
        event.t_ms = clock
        clock += event.dwell_ms


def inject_friction(
    events: list[Event],
    rng: random.Random,
    kind: str | None = None,
    index: int | None = None,
) -> tuple[int, str]:
    """Corrupt exactly one step. Returns the (index, kind) ground truth.

    The final step is never chosen for UI-side friction: friction on
    `tx_success` has nothing after it to be frustrated about. `silent_failure`
    is narrower still and can only land on an AWAIT_STEPS index.
    """
    if kind is None:
        kind = rng.choice(FRICTION_KINDS)

    if kind == "silent_failure":
        if index is None:
            candidates = [e.index for e in events if e.step in AWAIT_STEPS]
            index = rng.choice(candidates)
    elif index is None:
        index = rng.randrange(len(events) - 1)

    event = events[index]
    pool = _STEP_ERRORS.get(event.step) or ["ERR_UNKNOWN"]

    if kind == "stall":
        # The user sits on a dead screen - a spinner that never resolves. The
        # low end overlaps a distracted user, so the shortest stalls are
        # genuinely hard to call.
        event.dwell_ms = rng.randint(35_000, 90_000)
        event.ui_state = "pending"

    elif kind == "error_loop":
        # Same failure over and over: the allowance/slippage trap. Starts at 2,
        # inside the benign 1-3 range, so a low-retry loop is ambiguous.
        event.retries = rng.randint(2, 6)
        event.errors = [rng.choice(pool)] * event.retries
        event.dwell_ms += rng.randint(4_000, 12_000)
        event.rage_clicks += rng.randint(1, 4)
        event.ui_state = "error"
        if event.chain is not None:
            event.chain["status"] = "reverted"

    elif kind == "rage_click":
        # The button looks dead, so they hammer it. Starts at 4, which an
        # ordinarily impatient user also reaches.
        event.rage_clicks = rng.randint(4, 15)
        event.dwell_ms += rng.randint(1_500, 5_000)
        event.retries = rng.randint(0, 2)

    elif kind == "silent_failure":
        # The transaction settled. The UI never found out. No error is raised,
        # nothing looks broken, and the user waits on a spinner for a result
        # they already have. Invisible to UI-only analytics - the mismatch
        # between ui_state and chain.status is the only evidence.
        #
        # Dwell stays inside the plausible range on purpose: pushing it to
        # stall-like values would make this detectable on timing alone and
        # collapse it into the `stall` case.
        event.dwell_ms = rng.randint(18_000, 34_000)
        event.ui_state = "pending"
        event.chain = {"tx_hash": _tx_hash(rng), "status": "confirmed"}
        event.rage_clicks += rng.choice([0, 0, 1, 2])

    else:
        raise ValueError(f"unknown friction kind: {kind!r}")

    _recompute_timeline(events)
    return index, kind


def generate_task(
    seed: int | None = None,
    kind: str | None = None,
) -> tuple[dict, dict]:
    """Produce one (challenge, answer) pair.

    The challenge is what gets sent to miners. The answer stays with the
    validator and is used to score the responses that come back.
    """
    rng = random.Random(seed)
    events = _healthy_events(rng)
    friction_index, friction_kind = inject_friction(events, rng, kind=kind)

    challenge = {
        "session_id": str(uuid.UUID(int=rng.getrandbits(128), version=4)),
        "flow": "token_swap",
        "steps": [step for step, _, _ in SWAP_FLOW],
        "events": [asdict(event) for event in events],
    }
    answer = {"friction_index": friction_index, "friction_kind": friction_kind}
    return challenge, answer


def generate_organic(seed: int | None = None) -> dict:
    """A session with no ground truth, standing in for real dApp traffic.

    In production this does not exist: an organic session arrives from a live
    dApp and nobody knows where its friction is. Here we fabricate one and
    deliberately discard the answer, so the validator genuinely cannot score
    the reply even if it wanted to.

    Crucially it is byte-identical in shape to a scored challenge. A miner
    that could tell the two apart would do careful work only when being
    marked, so the distinction stays on the validator's side of the wire.
    """
    challenge, _discarded_answer = generate_task(seed=seed)
    return challenge


def render_session(challenge: dict, answer: dict | None = None) -> str:
    """Human-readable dump of a session, for eyeballing what we generate."""
    marked = answer["friction_index"] if answer else None
    header = (
        f"   {'step':<16}{'t_ms':>8}{'dwell':>8}{'retry':>7}{'rage':>6}"
        f"  {'ui':<8}{'chain':<11}errors"
    )
    lines = [
        f"session {challenge['session_id']}  flow={challenge['flow']}",
        header,
    ]
    for event in challenge["events"]:
        flag = "-> " if event["index"] == marked else "   "
        errors = ",".join(event["errors"][:1]) or "-"
        if len(event["errors"]) > 1:
            errors += f" (x{len(event['errors'])})"
        chain = event["chain"]["status"] if event["chain"] else "-"
        lines.append(
            f"{flag}{event['step']:<16}{event['t_ms']:>8}{event['dwell_ms']:>8}"
            f"{event['retries']:>7}{event['rage_clicks']:>6}"
            f"  {event['ui_state']:<8}{chain:<11}{errors}"
        )
    if answer:
        step = challenge["events"][answer["friction_index"]]["step"]
        lines.append(
            f"   ground truth: index {answer['friction_index']} "
            f"({answer['friction_kind']}) at step '{step}'"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    # One sample of each friction kind, seeded so the output is reproducible.
    for n, friction in enumerate(FRICTION_KINDS):
        challenge, answer = generate_task(seed=1000 + n, kind=friction)
        print(f"\n=== {friction} ===")
        print(render_session(challenge, answer))

    print("\n=== what a miner receives for one await step ===")
    challenge, answer = generate_task(seed=7, kind="silent_failure")
    print(json.dumps(challenge["events"][answer["friction_index"]], indent=2))

    # The label-leak check. A cheating miner answers "whichever event isn't
    # ui_state == ok" and does no analysis at all. If that scores well, the
    # generator is tagging its own answers and the subnet measures nothing.
    # This stays in as a regression test: if it ever climbs back toward 1.0,
    # a label has leaked into the challenge again.
    print("\n=== naive baseline: first event with ui_state != 'ok' ===")
    trials = 2_000
    hits = 0
    guessable = 0
    for seed in range(trials):
        challenge, answer = generate_task(seed=seed)
        guess = next(
            (e["index"] for e in challenge["events"] if e["ui_state"] != "ok"),
            None,
        )
        if guess is not None:
            guessable += 1
            hits += guess == answer["friction_index"]
    print(f"  sessions            : {trials}")
    print(f"  cheat rule fired on : {guessable} ({guessable / trials:.0%})")
    print(f"  accuracy            : {hits / trials:.1%}")
    print(f"  random-guess floor  : {1 / len(SWAP_FLOW):.1%}")
