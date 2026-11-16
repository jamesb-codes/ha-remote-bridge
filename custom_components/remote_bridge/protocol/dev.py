"""Standalone dev receiver: advertise, pair, and print decoded inputs.

PYTHONPATH=custom_components/remote_bridge python -m protocol.dev --name "Test TV"
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import socket
from pathlib import Path

from zeroconf import IPVersion
from zeroconf.asyncio import AsyncServiceInfo, AsyncZeroconf

from .companion_server import ClientConnection, CompanionServer
from .hap_pairing import PairingPolicy
from .hid import RawInput
from .identity import JsonFileState, PairedClient
from .mdns import StubListeners, service_specs


def detect_local_ip() -> str:
    """LAN IPv4 via the UDP-connect trick (atv-core server.rs:519-543). Sends nothing."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.connect(("192.0.2.1", 9))
        return str(sock.getsockname()[0])


async def run(args: argparse.Namespace) -> None:
    state = JsonFileState(Path(args.state_file), args.name)
    identity = state.identity
    if identity.name != args.name:
        identity.name = args.name
        state._changed()

    def on_pin(pin: str) -> None:
        print(f"\n  >>> Pairing PIN for {identity.name}: {pin} <<<\n", flush=True)

    def on_paired(client: PairedClient) -> None:
        print(f"[paired] {client.name or client.client_id}", flush=True)

    def on_input(raw: RawInput) -> None:
        print(f"[input] {raw.client}: {raw}", flush=True)

    def on_session(conn: ClientConnection, active: bool) -> None:
        print(f"[session] {conn.client_name} {'started' if active else 'ended'}", flush=True)

    server = CompanionServer(
        identity,
        state,
        on_input=on_input,
        policy=PairingPolicy(allow_pairing=lambda: not args.no_pairing, on_pin=on_pin, on_paired=on_paired),
        on_session_change=on_session,
        swipe_threshold=args.swipe_threshold,
    )
    port = await server.start(port=args.port)
    stubs: StubListeners | None = None
    mrp_port = airplay_port = None
    if args.with_stubs:
        stubs = StubListeners()
        mrp_port, airplay_port = await stubs.start()

    ip = args.ip or detect_local_ip()
    host = identity.name.replace(" ", "-") + ".local."
    infos = [
        AsyncServiceInfo(
            spec.service_type,
            f"{identity.name}.{spec.service_type}",
            addresses=[socket.inet_aton(ip)],
            port=spec.port,
            properties=spec.properties,
            server=host,
        )
        for spec in service_specs(identity, port, mrp_port, airplay_port)
    ]
    zc = AsyncZeroconf(ip_version=IPVersion.V4Only, interfaces=[ip])
    for info in infos:
        await zc.async_register_service(info, allow_name_change=True)

    print(f"Advertising '{identity.name}' on {ip}")
    stub_info = "" if not args.with_stubs else f", MRP stub {mrp_port}, AirPlay stub {airplay_port}"
    print(f"  Companion port {port}{stub_info}")
    print(f"  Paired devices: {len(state.clients)}; new pairings {'OFF' if args.no_pairing else 'allowed'}")
    print("Open Control Center > Remote on the iPhone and pick this device. Ctrl+C to quit.")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    print("Shutting down…")
    for info in infos:
        await zc.async_unregister_service(info)
    await zc.async_close()
    await server.stop()
    if stubs is not None:
        await stubs.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--name", default="Test TV", help="name shown on the phone")
    parser.add_argument("--state-file", default="remote_bridge_dev.json", help="identity + keys (JSON)")
    parser.add_argument("--port", type=int, default=0, help="Companion TCP port (0 = any free port)")
    parser.add_argument("--ip", help="LAN IPv4 to advertise (default: auto-detect)")
    parser.add_argument(
        "--with-stubs",
        action="store_true",
        help="also advertise MRP/AirPlay stubs (not needed on iOS 27)",
    )
    parser.add_argument("--no-pairing", action="store_true", help="refuse new pairings")
    parser.add_argument("--swipe-threshold", type=float, default=150.0, help="swipe distance (0-1000)")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging (shows every message)")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.verbose:
        logging.getLogger("zeroconf").setLevel(logging.WARNING)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
