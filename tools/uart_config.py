#!/usr/bin/env python3
"""Read or change PerryNet UART settings."""

from __future__ import annotations

import argparse
import os
import sys

from perrynet_serial import OP_UART_GET, OP_UART_SET, PerryNetClient, PerryNetError


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=os.environ.get("PORT", "/dev/ttyUSB0"),
                    help="serial port, default: PORT env or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=int(os.environ.get("BAUD", "9600")),
                    help="current serial baud rate, default: BAUD env or 9600")
    ap.add_argument("--set-baud", type=int, default=None,
                    help="new firmware baud rate")
    flow = ap.add_mutually_exclusive_group()
    flow.add_argument("--rtscts", action="store_true", help="enable RTS/CTS")
    flow.add_argument("--no-rtscts", action="store_true", help="disable RTS/CTS")
    ap.add_argument("--save", action="store_true",
                    help="save UART settings to ESP8266 EEPROM")
    return ap


def parse_get(payload: bytes) -> tuple[int, int]:
    if len(payload) < 5:
        raise PerryNetError("short UART_GET response")
    return int.from_bytes(payload[:4], "little"), payload[4]


def main() -> int:
    args = parser().parse_args()
    try:
        with PerryNetClient(args.port, args.baud) as client:
            client.drain()
            major, minor, *_rest, name = client.hello()
            print(f"device: {name} v{major}.{minor}")

            baud, flags = parse_get(client.command(OP_UART_GET))
            print(f"uart: baud={baud} rtscts={int(bool(flags & 0x01))}")

            if args.set_baud is None and not args.rtscts and not args.no_rtscts:
                return 0

            new_baud = args.set_baud if args.set_baud is not None else baud
            new_flags = flags
            if args.rtscts:
                new_flags |= 0x01
            if args.no_rtscts:
                new_flags &= ~0x01
            if args.save:
                new_flags |= 0x02

            payload = new_baud.to_bytes(4, "little") + bytes((new_flags,))
            client.command(OP_UART_SET, payload)
            print(f"uart: set baud={new_baud} rtscts={int(bool(new_flags & 0x01))} "
                  f"saved={int(args.save)}")
            if new_baud != args.baud:
                print(f"note: reconnect at {new_baud} baud")
    except (OSError, PerryNetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
