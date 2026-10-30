"""FlowState gateway: the door a dApp actually sends telemetry through.

Until now the organic stream was a function inside the validator that
fabricated a session. This is the missing piece: an HTTP endpoint a real
front end posts a real session to, which relays it to the miners over the
same signed transport the scored stream uses and hands their reports back.

It serves the demo front end itself, from the same origin as the API. That is
deliberate rather than lazy - a page served from anywhere else needs CORS, and
a page hosted off this machine cannot reach a validator on localhost at all.

Organic sessions are **never scored**. Nothing here touches weights. When the
operator arms a friction for a demo the known answer is shown beside the
miners' verdicts so a room can see who was right, but that comparison is for
humans in the room, not an input to consensus.

Run:
    python -m neurons.gateway --miner miner=http://127.0.0.1:8091 \
                              --miner miner_naive=http://127.0.0.1:8092
    # then open http://127.0.0.1:8099
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import time
import uuid
from collections import defaultdict, deque
from dataclasses import asdict
from pathlib import Path

import bittensor as bt
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from flowstate.telemetry import (
    AWAIT_STEPS,
    FRICTION_KINDS,
    SWAP_FLOW,
    Event,
    _recompute_timeline,
)
from neurons.validator import MinerEndpoint, discover_miners, query_miner, resolve_hotkey

log = logging.getLogger("flowstate.gateway")

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
STEP_NAMES = [step for step, _, _ in SWAP_FLOW]

# The metagraph and the on-chain weight matrix change on epoch boundaries, not
# per request, so a short cache keeps the panel live without a chain round-trip
# on every keystroke.
CHAIN_CACHE_S = 25.0

# The browser leg carries no authentication. That is fine on loopback and not
# fine behind a public tunnel, where the URL is crawlable and every /api/ingest
# call fans out to every miner. These caps are the floor: enough for a demo,
# not enough to use the miners as someone else's compute.
INGEST_PER_MINUTE = 20          # per client
INGEST_BURST_GLOBAL = 60        # across all clients, same window
MAX_EVENTS_PER_SESSION = 64     # a swap is 8; this only stops absurd payloads

_hits: dict[str, deque[float]] = defaultdict(deque)
_all_hits: deque[float] = deque()


def _client_key(request: Request) -> str:
    """Who is calling, as well as we can tell.

    Behind cloudflared the socket peer is Cloudflare, so the real caller is in
    X-Forwarded-For. That header is trivially spoofable when nothing trusted
    sets it, which means this is a courtesy limit against accidental load and
    casual abuse - not a security control. Anything stronger needs a real token.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_check(request: Request) -> None:
    now = time.monotonic()
    cutoff = now - 60.0

    while _all_hits and _all_hits[0] < cutoff:
        _all_hits.popleft()
    if len(_all_hits) >= INGEST_BURST_GLOBAL:
        raise HTTPException(429, "gateway busy — try again in a minute")

    key = _client_key(request)
    window = _hits[key]
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= INGEST_PER_MINUTE:
        raise HTTPException(
            429, f"rate limit: {INGEST_PER_MINUTE} sessions per minute"
        )

    window.append(now)
    _all_hits.append(now)
    # Keep the per-client table from growing without bound on a public URL.
    if len(_hits) > 4096:
        for stale in [k for k, w in _hits.items() if not w]:
            del _hits[stale]


class BrowserEvent(BaseModel):
    """One step as the front end observed it. The chain half is added here."""

    step: str
    dwell_ms: int = Field(ge=0)
    retries: int = Field(ge=0, default=0)
    rage_clicks: int = Field(ge=0, default=0)
    ui_state: str = "ok"


class IngestRequest(BaseModel):
    events: list[BrowserEvent]
    flow: str = "token_swap"
    # Operator-armed friction, for a live demo. Both absent means "send exactly
    # what the user did", which is the real product path. The front end names
    # the step because it is the one that staged the experience there.
    arm_kind: str | None = None
    arm_step: str | None = None


class State:
    def __init__(self, wallet: bt.Wallet, netuid: int) -> None:
        self.wallet = wallet
        self.netuid = netuid
        self.endpoints: list[MinerEndpoint] = []
        self.client: bt.Subtensor | None = None
        self.chain: dict = {}
        self.chain_at = 0.0
        self.served = 0


state: State | None = None
app = FastAPI(title="FlowState gateway")


def _tx_hash(rng: random.Random) -> str:
    digits = "0123456789abcdef"
    return "0x" + "".join(rng.choice(digits) for _ in range(64))


def build_session(payload: IngestRequest) -> tuple[dict, list[dict]]:
    """Turn what the browser saw into a session the miners can read.

    The front end reports the interface half only, because a browser has no
    privileged view of the chain. This fills in the chain half, which is the
    entire point of the subnet.

    An armed friction is applied here as a chain-side edit only - never by
    re-running the generator's injection, which would replace the real dwell
    times and click counts the browser just measured with synthetic ones and
    throw away the only thing that makes this an organic session. The browser
    already staged the experience; all that is missing is the half of the
    story a browser cannot see.
    """
    rng = random.Random()
    events: list[Event] = []
    for index, incoming in enumerate(payload.events):
        if incoming.step not in STEP_NAMES:
            raise HTTPException(422, f"unknown step {incoming.step!r}")
        chain = None
        if incoming.step in AWAIT_STEPS:
            # A step still showing a spinner reports pending on both sides;
            # anything settled reports confirmed. A disagreement is only ever
            # produced by a real desync, or by an armed injection below.
            status = "pending" if incoming.ui_state == "pending" else "confirmed"
            chain = {"tx_hash": _tx_hash(rng), "status": status}
        events.append(
            Event(
                index=index,
                step=incoming.step,
                t_ms=0,
                dwell_ms=incoming.dwell_ms,
                ui_state=incoming.ui_state,
                retries=incoming.retries,
                rage_clicks=incoming.rage_clicks,
                chain=chain,
            )
        )

    truth: list[dict] = []
    if payload.arm_kind:
        if payload.arm_kind not in FRICTION_KINDS:
            raise HTTPException(422, f"unknown friction {payload.arm_kind!r}")
        target = next(
            (e for e in events if e.step == payload.arm_step), None
        )
        if target is None:
            raise HTTPException(422, f"armed step {payload.arm_step!r} not in session")

        if payload.arm_kind == "silent_failure":
            # The one case only the chain can reveal: it settled, and the
            # interface is still spinning.
            target.ui_state = "pending"
            target.chain = {"tx_hash": _tx_hash(rng), "status": "confirmed"}
        elif payload.arm_kind == "error_loop":
            target.ui_state = "error"
            if target.chain is not None:
                target.chain["status"] = "reverted"
        # stall and rage_click need no chain-side edit at all: the browser's
        # real dwell and click counts already carry them.

        truth.append(
            {
                "index": target.index,
                "kind": payload.arm_kind,
                "step": target.step,
            }
        )

    _recompute_timeline(events)
    session = {
        "session_id": str(uuid.uuid4()),
        "flow": payload.flow,
        "steps": STEP_NAMES,
        "events": [asdict(event) for event in events],
    }
    return session, truth


async def refresh_chain(force: bool = False) -> dict:
    """Metagraph plus the revealed weight matrix, lightly cached."""
    assert state is not None and state.client is not None
    if not force and state.chain and (time.monotonic() - state.chain_at) < CHAIN_CACHE_S:
        return state.chain

    metagraph = await state.client.subnets.metagraph(netuid=state.netuid)
    try:
        weights = await state.client.weights.weights(netuid=state.netuid)
    except Exception as exc:
        log.warning("weights read failed: %s", exc)
        weights = {}

    # Sum each miner's column across every validator row that voted for it.
    incoming: dict[int, float] = {}
    for row in weights.values():
        for uid, value in row.items():
            incoming[int(uid)] = incoming.get(int(uid), 0.0) + float(value)

    self_hotkey = state.wallet.hotkey.ss58_address
    state.chain = {
        "netuid": metagraph.netuid,
        "name": metagraph.name,
        "block": metagraph.block,
        "tempo": metagraph.tempo,
        "neurons": [
            {
                "uid": n.uid,
                "hotkey": n.hotkey,
                "axon": n.axon,
                "is_validator": n.hotkey == self_hotkey,
                "validator_permit": n.validator_permit,
                "weight": round(incoming.get(n.uid, 0.0), 4),
            }
            for n in metagraph.neurons
        ],
    }
    state.chain_at = time.monotonic()
    return state.chain


@app.get("/")
async def index() -> FileResponse:
    page = WEB_ROOT / "index.html"
    if not page.exists():
        raise HTTPException(500, f"front end missing at {page}")
    return FileResponse(page)


@app.get("/api/meta")
async def meta() -> dict:
    """Everything the front end needs to render itself."""
    assert state is not None
    return {
        "steps": STEP_NAMES,
        "frictions": list(FRICTION_KINDS),
        "await_steps": list(AWAIT_STEPS),
        "miners": [
            {"uid": e.uid, "hotkey": e.hotkey, "url": e.url} for e in state.endpoints
        ],
    }


@app.get("/api/chain")
async def chain() -> dict:
    try:
        return await refresh_chain()
    except Exception as exc:
        log.warning("chain read failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.post("/api/ingest")
async def ingest(payload: IngestRequest, request: Request) -> dict:
    """Relay one real session to every miner and return their reports."""
    assert state is not None
    _rate_check(request)
    if not payload.events:
        raise HTTPException(422, "a session needs at least one event")
    if len(payload.events) > MAX_EVENTS_PER_SESSION:
        raise HTTPException(
            422, f"at most {MAX_EVENTS_PER_SESSION} events per session"
        )

    session, truth = build_session(payload)

    async def ask(endpoint: MinerEndpoint) -> dict:
        started = time.perf_counter()
        reply = await query_miner(state.wallet, endpoint, session)
        latency = round((time.perf_counter() - started) * 1000)
        return {
            "uid": endpoint.uid,
            "hotkey": endpoint.hotkey,
            "latency_ms": latency,
            "reachable": reply is not None,
            "findings": [f.model_dump() for f in reply.findings] if reply else [],
        }

    verdicts = await asyncio.gather(*(ask(e) for e in state.endpoints))
    state.served += 1
    log.info(
        "organic session %s relayed to %d miner(s)%s",
        session["session_id"][:8],
        len(state.endpoints),
        f" (armed {payload.arm_kind} at {payload.arm_step})" if payload.arm_kind else "",
    )
    return {
        "session_id": session["session_id"],
        "events": session["events"],
        "verdicts": list(verdicts),
        # Present only when the operator armed a friction. Shown next to the
        # verdicts so a room can see who was right; never used for scoring.
        "armed_truth": truth,
        "scored": False,
    }


async def run(args: argparse.Namespace) -> None:
    global state
    wallet = bt.Wallet(name=args.wallet, hotkey=args.hotkey)
    state = State(wallet, args.netuid)
    log.info("gateway validator hotkey %s", wallet.hotkey.ss58_address)

    overrides: list[tuple[str, str]] = []
    for spec in args.miner or ():
        if "=" not in spec:
            raise SystemExit(f"--miner expects HOTKEY=URL, got {spec!r}")
        token, url = spec.split("=", 1)
        overrides.append((resolve_hotkey(args.wallet, token), url))

    async with bt.Subtensor(args.network) as client:
        state.client = client
        state.endpoints = await discover_miners(
            client, args.netuid, wallet.hotkey.ss58_address, overrides
        )
        if not state.endpoints:
            raise SystemExit(
                "no miners to relay to. Run miners that publish an axon, or name "
                "them: --miner miner=http://127.0.0.1:8091"
            )
        await refresh_chain(force=True)

        server = uvicorn.Server(
            uvicorn.Config(app, host=args.host, port=args.port, log_level="warning")
        )
        log.info("gateway on http://%s:%d", args.host, args.port)
        await server.serve()


def main() -> None:
    parser = argparse.ArgumentParser(description="FlowState telemetry gateway")
    parser.add_argument("--netuid", type=int, default=600)
    parser.add_argument("--network", default="test")
    parser.add_argument("--wallet", default="flowstate")
    parser.add_argument("--hotkey", default="validator")
    # Bound to loopback by default: the browser leg carries no authentication,
    # which is fine for a local demo and not fine on a public interface.
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--miner", action="append", metavar="HOTKEY=URL")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log.info("shutting down")


if __name__ == "__main__":
    main()
