"""Demo-day pre-flight for FlowState.

`healthcheck.sh` answers "is the code sound". This answers "will a live run
work *right now*", which is a different question with different failure modes -
the ones that bite during a demo rather than before it:

  * weights are rate limited to one commit per N blocks, so a rehearsal with
    --set-weights can make the real run fail minutes later;
  * published axons store an IP, and WSL reassigns its IP across reboots, so
    the addresses on chain can point at a host that no longer exists;
  * a miner can be registered and published and simply not be running.

Reads chain state and opens TCP connections. Writes nothing, spends nothing.

    python -m scripts.preflight
    python -m scripts.preflight --require-miners   # unreachable miner = failure

Exit code 0 if ready, 1 if not.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import socket

import bittensor as bt

OK = "ok  "
WARN = "WARN"
BAD = "FAIL"


class Report:
    """Collects findings so the verdict reflects everything, not just the last."""

    def __init__(self) -> None:
        self.failures = 0
        self.warnings = 0

    def line(self, status: str, label: str, detail: str) -> None:
        if status == BAD:
            self.failures += 1
        elif status == WARN:
            self.warnings += 1
        print(f"  [{status}] {label:<22} {detail}")


def host_ips() -> set[str]:
    """Every IPv4 address this host answers on, for comparing against chain."""
    found = {"127.0.0.1"}
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            found.add(info[4][0])
    except OSError:
        pass
    try:
        # The address used to reach the outside world, which is the one WSL
        # reassigns on restart and therefore the one that goes stale on chain.
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        with probe:
            probe.connect(("1.1.1.1", 80))
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    return found


async def reachable(host: str, port: int, timeout: float = 3.0) -> bool:
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def maybe_await(value):
    return await value if inspect.isawaitable(value) else value


async def main() -> int:
    parser = argparse.ArgumentParser(description="FlowState demo pre-flight")
    parser.add_argument("--netuid", type=int, default=600)
    parser.add_argument("--network", default="test")
    parser.add_argument("--wallet", default="flowstate")
    parser.add_argument("--hotkey", default="validator")
    parser.add_argument(
        "--require-miners",
        action="store_true",
        help="treat an unreachable published miner as a failure, not a warning",
    )
    args = parser.parse_args()

    report = Report()
    wallet = bt.Wallet(name=args.wallet, hotkey=args.hotkey)
    self_hotkey = wallet.hotkey.ss58_address
    ips = host_ips()

    async with bt.Subtensor(args.network) as client:
        metagraph = await client.subnets.metagraph(netuid=args.netuid)

        print(f"\nFlowState pre-flight - netuid {args.netuid} "
              f"({metagraph.name or 'unnamed'})\n")

        print(" chain")
        report.line(OK, "block", str(metagraph.block))
        report.line(
            OK if metagraph.name and metagraph.name != "unknown" else WARN,
            "subnet identity",
            f"{metagraph.name!r} (tempo {metagraph.tempo})",
        )
        report.line(OK, "neurons", f"{metagraph.num_uids}/{metagraph.max_uids}")

        # Note: client.balance() is a Balance *constructor*, not a query.
        # The read lives on the balances namespace.
        try:
            balance = await maybe_await(
                client.balances.balance(wallet.coldkeypub.ss58_address)
            )
            report.line(OK, "coldkey balance", str(balance))
        except Exception as exc:
            report.line(WARN, "coldkey balance", f"unreadable: {exc}")

        # --- validator ------------------------------------------------------
        print("\n validator")
        me = metagraph.by_hotkey(self_hotkey)
        if me is None:
            report.line(BAD, "registered", f"{self_hotkey[:12]}.. not on subnet")
            print()
            return 1
        report.line(OK, "uid", str(me.uid))
        report.line(
            OK if me.validator_permit else BAD,
            "validator permit",
            "yes" if me.validator_permit else "NO - cannot set weights",
        )

        rate_limit = None
        try:
            rate_limit = await maybe_await(
                client.hyperparameters.weights_rate_limit(netuid=args.netuid)
            )
        except Exception as exc:
            report.line(WARN, "weights rate limit", f"unreadable: {exc}")

        elapsed = metagraph.block - me.last_update
        if rate_limit is not None:
            rate_limit = int(rate_limit)
            remaining = rate_limit - elapsed
            if remaining > 0:
                # ~12s blocks on this chain.
                report.line(
                    BAD,
                    "weights settable",
                    f"NO - {remaining} blocks to wait (~{remaining * 12 // 60} min). "
                    f"Last set at block {me.last_update}.",
                )
            else:
                report.line(
                    OK,
                    "weights settable",
                    f"yes - last set {elapsed} blocks ago, limit {rate_limit}",
                )

        # The authoritative answer: ask the chain to validate the real call.
        miner_uids = [n.uid for n in metagraph.neurons if n.uid != me.uid]
        if miner_uids:
            intent = bt.SetWeights(
                netuid=args.netuid,
                uids=miner_uids,
                weights=[1.0] * len(miner_uids),
            )
            try:
                await client.preflight(intent, wallet)
                report.line(OK, "SetWeights preflight", "accepted (not submitted)")
            except Exception as exc:
                report.line(BAD, "SetWeights preflight",
                            f"{type(exc).__name__}: {exc}")

        # --- miners ---------------------------------------------------------
        print("\n miners")
        published = [
            n for n in metagraph.neurons if n.uid != me.uid and n.axon
        ]
        silent = [n for n in metagraph.neurons if n.uid != me.uid and not n.axon]

        if not published:
            report.line(
                WARN,
                "published axons",
                "none - DISCOVER mode will find nothing; use --miner HOST=URL",
            )
        for neuron in published:
            host, _, port = neuron.axon.rpartition(":")
            label = f"uid {neuron.uid}"
            if host not in ips:
                report.line(
                    BAD, label,
                    f"axon {neuron.axon} - IP is NOT this host {sorted(ips)}. "
                    f"Stale after a reboot; re-publish with btcli tx serve-axon.",
                )
                continue
            if await reachable(host, int(port)):
                report.line(OK, label, f"axon {neuron.axon} - reachable")
            else:
                report.line(
                    BAD if args.require_miners else WARN, label,
                    f"axon {neuron.axon} - IP correct but nothing listening "
                    f"(is the miner running?)",
                )
        for neuron in silent:
            report.line(WARN, f"uid {neuron.uid}",
                        "registered but no axon published")

        print(f"\n host IPv4: {', '.join(sorted(ips))}")

    print()
    if report.failures:
        print(f"  VERDICT: NOT READY - {report.failures} failure(s), "
              f"{report.warnings} warning(s)\n")
        return 1
    print(f"  VERDICT: ready ({report.warnings} warning(s))\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
