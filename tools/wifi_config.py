#!/usr/bin/env python3
"""Configure PerryNet WiFi credentials over serial."""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from perrynet_serial import (
    OP_SETTINGS_SAVE,
    OP_WIFI_CONNECT,
    PerryNetClient,
    PerryNetError,
    PerryNetTimeout,
)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=os.environ.get("PORT", "/dev/ttyUSB0"),
                    help="serial port, default: PORT env or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=int(os.environ.get("BAUD", "9600")),
                    help="serial baud rate, default: BAUD env or 9600")
    ap.add_argument("--ssid", default=os.environ.get("SSID"),
                    help="WiFi SSID, default: SSID env")
    ap.add_argument("--password", default=os.environ.get("PASS"),
                    help="WiFi password, default: PASS env")
    ap.add_argument("--no-save", action="store_true",
                    help="do not persist credentials to ESP8266 EEPROM")
    ap.add_argument("--no-connect", action="store_true",
                    help="configure credentials but do not connect now")
    ap.add_argument("--timeout", type=float, default=35.0,
                    help="seconds to wait for WiFi connection")
    return ap


def main() -> int:
    args = parser().parse_args()
    ssid = args.ssid or input("SSID: ")
    password = args.password
    if password is None:
        password = getpass.getpass("Password: ")

    try:
        with PerryNetClient(args.port, args.baud) as client:
            client.drain()
            major, minor, max_payload, max_channels, max_listeners, features, name = client.hello()
            print(f"device: {name} v{major}.{minor} max_payload={max_payload} "
                  f"channels={max_channels} listeners={max_listeners} features=0x{features:08x}")

            client.set_wifi(ssid, password)
            print(f"wifi: stored SSID '{ssid}'")

            if not args.no_save:
                client.command(OP_SETTINGS_SAVE)
                print("settings: saved to EEPROM")

            if not args.no_connect:
                client.command(OP_WIFI_CONNECT)
                print("wifi: connecting...")
                status = client.wait_wifi(args.timeout)
                print(f"wifi: connected ip={status.ip} gateway={status.gateway} "
                      f"dns={status.dns} rssi={status.rssi}dBm")
            else:
                status = client.wifi_status()
                print(f"wifi: current status={status.status_name} connected={int(status.connected)}")
    except PerryNetTimeout as exc:
        print(f"error: {exc}", file=sys.stderr)
        if "WiFi did not connect" not in str(exc):
            print("hint: if this is a bare Wemos D1 over USB, PerryNet may have "
                  "RTS/CTS enabled. Temporarily tie D7/GPIO13 (CTS) to GND, then "
                  "run tools/uart_config.py --no-rtscts --save.", file=sys.stderr)
        return 1
    except (OSError, PerryNetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
