"""End-to-end test of the FlowState signed-request handshake.

This exercises the part Bittensor v11 no longer does for us: a validator signs
a request with its hotkey, the miner verifies the signature, checks the caller
against the metagraph, and only then does any work.

It runs entirely in-process against the FastAPI app and stubs the metagraph, so
no chain connection and no registered miner hotkey are needed. That matters:
the auth layer is where subtle mistakes live (hashing a re-serialised body,
signing a decoded path, replaying a nonce), and none of those need a chain to
catch.

Run:
    python -m tests.test_auth_loop
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import bittensor as bt
import httpx

import neurons.miner as miner_mod
from flowstate.protocol import (
    DETECT_PATH,
    JSON_HEADERS,
    DetectRequest,
    HotkeyAuth,
    canonical_body,
)
from flowstate.telemetry import generate_task

NETUID = 600
BASE_URL = "http://miner.invalid"   # never resolved; ASGITransport intercepts


@dataclass
class StubNeuron:
    """Just the fields the miner's caller policy reads."""

    uid: int
    hotkey: str
    validator_permit: bool


class StubMetagraph:
    """Stands in for the chain so the auth path can be tested offline."""

    def __init__(self, neurons: list[StubNeuron]) -> None:
        self.neurons = neurons
        self.num_uids = len(neurons)
        self.block = 0

    def by_hotkey(self, ss58: str) -> StubNeuron | None:
        return next((n for n in self.neurons if n.hotkey == ss58), None)


def make_client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=miner_mod.app)
    return httpx.AsyncClient(
        transport=transport,
        base_url=BASE_URL,
        timeout=10,
        headers=JSON_HEADERS,
    )


async def main() -> int:
    miner_wallet = bt.Wallet(name="flowstate", hotkey="miner")
    validator_wallet = bt.Wallet(name="flowstate", hotkey="validator")
    miner_hotkey = miner_wallet.hotkey.ss58_address
    validator_hotkey = validator_wallet.hotkey.ss58_address

    # Wire up the miner by hand, the way run() would, minus the chain.
    miner_mod.state = miner_mod.MinerState(
        miner_wallet, NETUID, require_permit=True
    )
    miner_mod.state.metagraph = StubMetagraph(
        [
            StubNeuron(uid=0, hotkey=validator_hotkey, validator_permit=True),
            StubNeuron(uid=1, hotkey=miner_hotkey, validator_permit=False),
        ]
    )

    challenge, answer = generate_task(seed=424242)
    request = DetectRequest(**{
        key: challenge[key] for key in ("session_id", "flow", "steps", "events")
    })
    body = canonical_body(request)

    results: list[tuple[str, bool, str]] = []

    def record(name: str, passed: bool, detail: str) -> None:
        results.append((name, passed, detail))

    async with make_client() as http:
        # --- 1. The happy path -------------------------------------------
        response = await http.post(
            DETECT_PATH,
            content=body,
            auth=HotkeyAuth(validator_wallet, receiver_ss58=miner_hotkey),
        )
        ok = response.status_code == 200
        detail = f"HTTP {response.status_code}"
        if ok:
            payload = response.json()
            # Deliberately not asserting the answer is right. This test is
            # about the handshake; whether a cold profile guesses correctly on
            # one session is the detector's business, measured properly over
            # thousands of sessions in flowstate.detect. Asserting it here
            # would make an auth test fail for a detection reason.
            ok = (
                isinstance(payload.get("friction_index"), int)
                and 0 <= payload["friction_index"] < len(request.events)
                and isinstance(payload.get("friction_kind"), str)
            )
            correct = payload["friction_index"] == answer["friction_index"]
            detail = (
                f"well-formed index={payload['friction_index']} "
                f"kind={payload['friction_kind']} "
                f"conf={payload['confidence']:.2f} "
                f"(truth {answer['friction_index']}, correct={correct})"
            )
        else:
            detail += f" {response.text[:120]}"
        record("signed request accepted and answered", ok, detail)

        # --- 2. No signature at all --------------------------------------
        response = await http.post(DETECT_PATH, content=body)
        record(
            "unsigned request rejected",
            response.status_code == 401,
            f"HTTP {response.status_code}",
        )

        # --- 3. Signed for a different receiver --------------------------
        # A signature naming another miner must not work here, or a response
        # captured from one miner could be aimed at another.
        response = await http.post(
            DETECT_PATH,
            content=body,
            auth=HotkeyAuth(validator_wallet, receiver_ss58=validator_hotkey),
        )
        record(
            "wrong-receiver signature rejected",
            response.status_code == 401,
            f"HTTP {response.status_code}",
        )

        # --- 4. Body tampered after signing ------------------------------
        headers = bt.http_auth.sign(
            validator_wallet,
            method="POST",
            path=DETECT_PATH,
            body=body,
            receiver_ss58=miner_hotkey,
        )
        tampered = body.replace(b'"dwell_ms":', b'"dwell_Ms":', 1)
        response = await http.post(DETECT_PATH, content=tampered, headers=headers)
        record(
            "tampered body rejected",
            response.status_code == 401,
            f"HTTP {response.status_code}",
        )

        # --- 5. Replay of a previously accepted signature ----------------
        # Headers are reused verbatim, which is exactly what a transport-level
        # retry would do. The nonce store must refuse the second one.
        headers = bt.http_auth.sign(
            validator_wallet,
            method="POST",
            path=DETECT_PATH,
            body=body,
            receiver_ss58=miner_hotkey,
        )
        first = await http.post(DETECT_PATH, content=body, headers=headers)
        second = await http.post(DETECT_PATH, content=body, headers=headers)
        record(
            "replayed signature rejected",
            first.status_code == 200 and second.status_code == 401,
            f"first HTTP {first.status_code}, replay HTTP {second.status_code}",
        )

        # --- 6. Caller not on the subnet ---------------------------------
        # Valid signature, unknown hotkey: authentication succeeds and
        # authorisation must still fail.
        miner_mod.state.metagraph = StubMetagraph([])
        response = await http.post(
            DETECT_PATH,
            content=body,
            auth=HotkeyAuth(validator_wallet, receiver_ss58=miner_hotkey),
        )
        record(
            "unregistered caller rejected",
            response.status_code == 403,
            f"HTTP {response.status_code}",
        )

        # --- 7. Registered caller without a validator permit -------------
        miner_mod.state.metagraph = StubMetagraph(
            [StubNeuron(uid=7, hotkey=validator_hotkey, validator_permit=False)]
        )
        response = await http.post(
            DETECT_PATH,
            content=body,
            auth=HotkeyAuth(validator_wallet, receiver_ss58=miner_hotkey),
        )
        record(
            "caller without permit rejected",
            response.status_code == 403,
            f"HTTP {response.status_code}",
        )

    width = max(len(name) for name, _, _ in results)
    failures = 0
    print()
    for name, passed, detail in results:
        mark = "PASS" if passed else "FAIL"
        failures += not passed
        print(f"  [{mark}] {name:<{width}}  {detail}")
    print(f"\n{len(results) - failures}/{len(results)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
