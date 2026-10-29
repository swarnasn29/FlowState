"""Wait until a hotkey's axon is discoverable on chain.

Exits 0 as soon as the metagraph reports a published endpoint for the hotkey,
or 1 if the timeout passes. Used by demo_chain.sh to decide whether the
validator can discover the miner from chain or needs a direct URL.

A publish is not instant, and the chain's serving rate limit can defer it for a
long while, so "not yet published" is a normal state rather than an error.

    python -m scripts.wait_for_axon --hotkey miner --timeout 90
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time

import bittensor as bt


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--netuid", type=int, default=600)
    parser.add_argument("--network", default="test")
    parser.add_argument("--wallet", default="flowstate")
    parser.add_argument("--hotkey", default="miner")
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--poll", type=float, default=6.0)
    args = parser.parse_args()

    ss58 = bt.Wallet(name=args.wallet, hotkey=args.hotkey).hotkey.ss58_address
    deadline = time.monotonic() + args.timeout

    async with bt.Subtensor(args.network) as client:
        while True:
            metagraph = await client.subnets.metagraph(netuid=args.netuid)
            neuron = metagraph.by_hotkey(ss58)
            if neuron is not None and neuron.axon:
                print(f"axon published: uid={neuron.uid} {neuron.axon}")
                return 0
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                uid = neuron.uid if neuron is not None else "unregistered"
                print(f"no axon on chain for {args.hotkey} (uid={uid})")
                return 1
            print(f"  waiting for axon... {remaining:.0f}s left")
            await asyncio.sleep(min(args.poll, remaining))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
