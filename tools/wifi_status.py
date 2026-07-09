#!/usr/bin/env python3
"""Check PerryNet WiFi status without starting a connection attempt."""

from __future__ import annotations

import argparse
import os
import sys
import time

from perrynet_serial import PerryNetClient, PerryNetError, PerryNetTimeout


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=os.environ.get("PORT", "/dev/ttyUSB0"),
                    help="serial port, default: PORT env or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=int(os.environ.get("BAUD", "9600")),
                    help="serial baud rate, default: BAUD env or 9600")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="seconds to poll before reporting failure")
    ap.add_argument("--interval", type=float, default=1.0,
                    help="seconds between WIFI_STATUS polls")
    ap.add_argument("--once", action="store_true",
                    help="print one WIFI_STATUS result and exit")
    return ap


def print_status(index: int, status) -> None:
    print(f"{index:02d}: wifi={status.status_name} connected={int(status.connected)} "
          f"ip={status.ip} dns={status.dns} rssi={status.rssi}dBm")


def main() -> int:
    args = parser().parse_args()
    try:
        with PerryNetClient(args.port, args.baud) as client:
            client.drain()
            major, minor, _max_payload, _max_channels, _max_listeners, _features, name = client.hello()
            print(f"device: {name} v{major}.{minor}")

            deadline = time.monotonic() + args.timeout
            index = 0
            while True:
                status = client.wifi_status()
                print_status(index, status)
                if status.connected:
                    return 0
                if args.once or time.monotonic() >= deadline:
                    return 1
                index += 1
                time.sleep(args.interval)
    except PerryNetTimeout as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, PerryNetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
