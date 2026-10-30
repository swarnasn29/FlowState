"""FlowState miner: serves friction detection over signed HTTP.

Bittensor v11 has no axon, so this is an ordinary FastAPI app. Two things make
it a Bittensor miner rather than a web service:

  * every request is authenticated against the caller's hotkey signature, and
    checked against the metagraph before any work is done;
  * the endpoint is published on chain with the ServeAxon intent, which is how
    validators discover where to reach it.

Run:
    python -m neurons.miner --netuid 600 --port 8091
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging

import bittensor as bt
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request

from flowstate import detect as detect_profiled
from flowstate import detect_naive
from flowstate.protocol import DETECT_PATH, DetectRequest, DetectResponse

log = logging.getLogger("flowstate.miner")

# Interchangeable detection strategies. Both expose a profile with `observe`
# and a `detect(challenge, profile)`, so the server does not care which it has.
# Running two miners with different strategies is how we show the subnet's
# weights separating a better miner from a worse one.
DETECTORS = {
    "profile": (detect_profiled.StepProfile, detect_profiled.analyse),
    "naive": (detect_naive.NaiveProfile, detect_naive.analyse),
}

# How often to re-read the metagraph. Registrations and permits change slowly,
# and every refresh is a chain round-trip, so this does not need to be tight.
METAGRAPH_REFRESH_S = 300


class MinerState:
    """Everything the request handler needs, in one place.

    The profile lives here and persists across requests on purpose: the miner
    learns what a normal dwell looks like from the traffic it serves. Roughly
    seven of every eight events it sees are healthy, which is clean enough for
    median/MAD to work without any separate training step.
    """

    def __init__(
        self,
        wallet: bt.Wallet,
        netuid: int,
        require_permit: bool,
        detector: str = "profile",
    ) -> None:
        self.wallet = wallet
        self.netuid = netuid
        self.require_permit = require_permit
        self.hotkey_ss58 = wallet.hotkey.ss58_address
        self.detector_name = detector
        profile_cls, self.detect = DETECTORS[detector]
        self.profile = profile_cls()
        self.metagraph: bt.Metagraph | None = None
        self.served = 0

    async def refresh_metagraph(self, client: bt.Subtensor) -> None:
        self.metagraph = await client.subnets.metagraph(netuid=self.netuid)
        log.info(
            "metagraph refreshed: %d uids at block %d",
            self.metagraph.num_uids,
            self.metagraph.block,
        )


state: MinerState | None = None
app = FastAPI(title="FlowState miner")


async def authenticated(request: Request) -> bt.http_auth.Caller:
    """Verify the signature, then decide whether this caller is allowed.

    Verifying identity and granting permission are separate steps. Anyone can
    hold a valid hotkey; only a hotkey registered on our subnet should be able
    to spend our CPU.
    """
    assert state is not None

    # Raw bytes, before any parsing - the hash has to match what was signed.
    raw_body = await request.body()

    # The signed target is the percent-encoded wire form, not the decoded path.
    target = request.scope["raw_path"].decode()
    query = request.scope["query_string"].decode()
    if query:
        target = f"{target}?{query}"

    try:
        caller = bt.http_auth.verify(
            request.headers,
            raw_body,
            method=request.method,
            path=target,
            self_hotkey_ss58=state.hotkey_ss58,
        )
    except bt.http_auth.AuthError as exc:
        # These messages never echo the signature or payload, so they are safe
        # to hand back to a caller we have not authenticated.
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    neuron = (
        state.metagraph.by_hotkey(caller.hotkey_ss58)
        if state.metagraph is not None
        else None
    )
    if neuron is None:
        raise HTTPException(status_code=403, detail="caller not registered on subnet")
    if state.require_permit and not neuron.validator_permit:
        raise HTTPException(status_code=403, detail="caller holds no validator permit")

    return caller


@app.post(DETECT_PATH, response_model=DetectResponse)
async def detect_friction(
    payload: DetectRequest,
    caller: bt.http_auth.Caller = Depends(authenticated),
) -> DetectResponse:
    """Locate the friction point in one session."""
    assert state is not None

    challenge = payload.model_dump()

    # Learn from the session before answering it. The validator's own task is
    # the miner's training data, which is the only data a miner ever gets.
    state.profile.observe(challenge)
    findings = state.detect(challenge, state.profile)
    state.served += 1

    # "clean" is a real answer, not a failure to answer: most sessions have no
    # friction in them, and saying so earns full marks.
    log.info(
        "served #%d for %s: %s",
        state.served,
        caller.hotkey_ss58[:10],
        ", ".join(
            f"{f.friction_index}:{f.friction_kind}@{f.confidence:.2f}"
            for f in findings
        ) or "clean",
    )
    return DetectResponse(findings=[finding.to_wire() for finding in findings])


@app.get("/health")
async def health() -> dict:
    assert state is not None
    return {
        "hotkey": state.hotkey_ss58,
        "netuid": state.netuid,
        "served": state.served,
        "metagraph_block": state.metagraph.block if state.metagraph else None,
    }


async def run(args: argparse.Namespace) -> None:
    global state

    wallet = bt.Wallet(name=args.wallet, hotkey=args.hotkey)
    state = MinerState(
        wallet,
        args.netuid,
        require_permit=not args.allow_unpermitted,
        detector=args.detector,
    )
    log.info("miner hotkey %s (detector: %s)", state.hotkey_ss58, args.detector)

    async with bt.Subtensor(args.network) as client:
        await state.refresh_metagraph(client)

        me = state.metagraph.by_hotkey(state.hotkey_ss58)
        if me is None:
            message = (
                f"hotkey {state.hotkey_ss58} is not registered on netuid "
                f"{args.netuid}. Register it with:\n"
                f"  btcli subnet register --netuid {args.netuid} "
                f"--network {args.network} --wallet {args.wallet} "
                f"--wallet-hotkey {args.hotkey}"
            )
            if args.require_registration:
                raise SystemExit(message)
            # An unregistered miner can still serve and be scored; it just
            # cannot receive weight, since weight is addressed by uid. Useful
            # for testing the full loop without paying the registration burn.
            log.warning("%s\ncontinuing unregistered (no weight can be earned)",
                        message)
        else:
            log.info("registered as uid %d", me.uid)

        async def publish_axon() -> None:
            """Advertise ip:port on chain. Runs alongside the server, not before it.

            This has to be concurrent with serving. The extrinsic can block for
            a long time when the chain's serving rate limit is in play, and a
            miner that answers nothing while it waits is losing score for no
            reason. Publishing starts no server either way - that is uvicorn's
            job - so the two are genuinely independent.

            Note the chain will not store a loopback address: publishing
            127.0.0.1 succeeds as an extrinsic but records ip=0, leaving the
            miner undiscoverable. Pass a routable --ip for a real deployment.
            """
            try:
                await client.execute(
                    bt.ServeAxon(netuid=args.netuid, ip=args.ip, port=args.port),
                    wallet,
                )
                log.info("published axon %s:%d on chain", args.ip, args.port)
            except Exception as exc:
                log.warning("ServeAxon failed (serving anyway): %s", exc)

        async def refresher() -> None:
            while True:
                await asyncio.sleep(METAGRAPH_REFRESH_S)
                try:
                    await state.refresh_metagraph(client)
                except Exception as exc:
                    log.warning("metagraph refresh failed: %s", exc)

        background = [asyncio.create_task(refresher())]
        if args.serve_axon:
            background.append(asyncio.create_task(publish_axon()))

        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host=args.host,
                port=args.port,
                log_level="warning",
                access_log=False,
            )
        )
        log.info("serving on %s:%d%s", args.host, args.port, DETECT_PATH)
        try:
            await server.serve()
        finally:
            for task in background:
                task.cancel()
            for task in background:
                with contextlib.suppress(asyncio.CancelledError):
                    await task


def main() -> None:
    parser = argparse.ArgumentParser(description="FlowState miner")
    parser.add_argument("--netuid", type=int, default=600)
    parser.add_argument("--network", default="test")
    parser.add_argument("--wallet", default="flowstate")
    parser.add_argument("--hotkey", default="miner")
    parser.add_argument("--host", default="0.0.0.0", help="interface to bind")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument(
        "--ip",
        default="127.0.0.1",
        help="IP to publish on chain; use your public IP for a real deployment",
    )
    parser.add_argument(
        "--no-serve-axon",
        dest="serve_axon",
        action="store_false",
        help="skip publishing ip:port on chain (useful for a local-only demo)",
    )
    parser.add_argument(
        "--allow-unpermitted",
        action="store_true",
        help="accept any registered caller, not just validator-permit holders",
    )
    parser.add_argument(
        "--skip-registration-check",
        dest="require_registration",
        action="store_false",
        help="serve even if this hotkey is not registered (local testing)",
    )
    parser.add_argument(
        "--detector",
        choices=tuple(DETECTORS),
        default="profile",
        help="detection strategy: 'profile' learns per-step norms, 'naive' "
             "applies global thresholds",
    )
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
