"""The wire contract between FlowState validators and miners.

Bittensor v11 removed `Synapse`, so there is no framework-provided request
object any more: the protocol is just HTTP with a signed body, and this module
is the single definition both sides agree on.

The one subtlety that matters: the signature covers the *exact bytes* of the
body. The validator signs what it is about to send and the miner verifies what
it actually received, hashing the raw bytes before parsing. If either side
re-serialises the JSON the hash changes and a legitimate request looks forged,
so both sides go through `canonical_body` and nothing else.
"""

from __future__ import annotations

import json

import bittensor as bt
import httpx
from pydantic import BaseModel, Field

PROTOCOL_VERSION = "flowstate/1"
DETECT_PATH = "/detect"

# The validator drops a miner that takes longer than this. Generous, because a
# miner building per-step statistics on a cold profile does real work, but
# bounded so one dead miner cannot stall a whole scoring round.
REQUEST_TIMEOUT_S = 8.0

# Sent on every request. We post pre-serialised bytes rather than letting the
# client encode a dict, because the signature covers those exact bytes - and
# raw bytes arrive with no content type, which FastAPI rejects with a 422
# before the handler ever runs. Easy to mistake for an auth failure.
JSON_HEADERS = {"content-type": "application/json"}


class DetectRequest(BaseModel):
    """A session handed to a miner to analyse.

    Deliberately identical whether the validator generated this session (and
    therefore knows the answer) or relayed it from a real dApp. A miner that
    could tell the two apart would answer carefully when scored and sloppily
    when not, so the scored/organic distinction is kept entirely on the
    validator side and never appears on the wire.
    """

    session_id: str
    flow: str
    steps: list[str]
    events: list[dict]


class DetectResponse(BaseModel):
    """A miner's verdict on where the friction is."""

    friction_index: int = Field(ge=0)
    friction_kind: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    version: str = PROTOCOL_VERSION


def canonical_body(payload: BaseModel | dict) -> bytes:
    """Serialise a payload to the exact bytes that get signed and hashed.

    Sorted keys and no incidental whitespace, so the same logical payload
    always produces the same bytes regardless of dict ordering.
    """
    data = payload.model_dump() if isinstance(payload, BaseModel) else payload
    return json.dumps(data, sort_keys=True, separators=(",", ":")).encode()


class HotkeyAuth(httpx.Auth):
    """Signs each outgoing request with the caller's hotkey.

    `requires_request_body` is essential, not optional: the signature covers a
    hash of the body, so httpx has to have assembled the body before the auth
    flow runs. Without it the hash would be taken over nothing and every
    request would be rejected.

    Bound to a specific receiver. A signature carries the hotkey it was issued
    for, so a response intercepted from one miner cannot be replayed against
    another - which also means a fresh `HotkeyAuth` is needed per miner.
    """

    requires_request_body = True

    def __init__(self, wallet: bt.Wallet, receiver_ss58: str) -> None:
        self.wallet = wallet
        self.receiver_ss58 = receiver_ss58

    def auth_flow(self, request: httpx.Request):
        headers = bt.http_auth.sign(
            self.wallet,
            method=request.method,
            # Must match what the server reconstructs from its ASGI scope:
            # the percent-encoded target including any query string.
            path=request.url.raw_path.decode(),
            body=request.content,
            receiver_ss58=self.receiver_ss58,
        )
        request.headers.update(headers)
        yield request
