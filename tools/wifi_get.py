#!/usr/bin/env python3
"""Show stored PerryNet WiFi credentials metadata."""

from __future__ import annotations

import argparse
import os
import sys

from perrynet_serial import PerryNetClient, PerryNetError, PerryNetTimeout


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=os.environ.get("PORT", "/dev/ttyUSB0"),
                    help="serial port, default: PORT env or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=int(os.environ.get("BAUD", "9600")),
                    help="serial baud rate, default: BAUD env or 9600")
    return ap


def main() -> int:
    args = parser().parse_args()
    try:
        with PerryNetClient(args.port, args.baud) as client:
            client.drain()
            major, minor, *_rest, name = client.hello()
            print(f"device: {name} v{major}.{minor}")
            ssid, pass_is_set = client.wifi_get()
            print(f"wifi: ssid='{ssid}' password_set={int(pass_is_set)}")
    except PerryNetTimeout as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, PerryNetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
