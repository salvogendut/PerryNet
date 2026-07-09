#!/usr/bin/env python3
"""Test that a configured PerryNet device can reach the internet."""

from __future__ import annotations

import argparse
import os
import sys
import time

from perrynet_serial import (
    EVT_TCP_CLOSED,
    EVT_TCP_ERROR,
    OP_EVENT,
    OP_TCP_DATA,
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
    ap.add_argument("--host", default="example.com", help="HTTP host to test")
    ap.add_argument("--path", default="/", help="HTTP path to fetch")
    ap.add_argument("--port-number", type=int, default=80, help="TCP port to test")
    ap.add_argument("--timeout", type=float, default=35.0,
                    help="overall network timeout in seconds")
    ap.add_argument("--pull-rx", action="store_true",
                    help="open TCP in host-pulled receive mode and poll TCP_RECV")
    return ap


def main() -> int:
    args = parser().parse_args()
    try:
        with PerryNetClient(args.port, args.baud) as client:
            client.drain()
            major, minor, _max_payload, _max_channels, _max_listeners, _features, name = client.hello()
            print(f"device: {name} v{major}.{minor}")

            status = client.wifi_status()
            if not status.connected:
                print(f"wifi: status={status.status_name}; connecting...")
                client.command(OP_WIFI_CONNECT)
                status = client.wait_wifi(args.timeout)
            print(f"wifi: connected ip={status.ip} dns={status.dns} rssi={status.rssi}dBm")
            clock = client.time_get()
            if clock.valid:
                print(f"time: unix_utc={clock.unix_utc} uptime_ms={clock.uptime_ms}")
            else:
                print(f"time: not synced yet uptime_ms={clock.uptime_ms}")

            ip = client.dns_resolve(args.host)
            print(f"dns: {args.host} -> {ip}")

            channel = client.tcp_open(args.host, args.port_number, pull_rx=args.pull_rx)
            print(f"tcp: connected channel={channel} {args.host}:{args.port_number}")

            request = (
                f"GET {args.path} HTTP/1.0\r\n"
                f"Host: {args.host}\r\n"
                "User-Agent: PerryNet-internet-test\r\n"
                "Connection: close\r\n"
                "\r\n"
            ).encode("ascii")
            written = client.tcp_send(channel, request)
            print(f"tcp: sent {written} bytes")

            deadline = time.monotonic() + args.timeout
            received = bytearray()
            closed = False
            if args.pull_rx:
                last_data = time.monotonic()
                while time.monotonic() < deadline:
                    chunk = client.tcp_recv(channel, 64)
                    if chunk:
                        received.extend(chunk)
                        last_data = time.monotonic()
                        print(f"tcp/pull: received {len(chunk)} bytes ({len(received)} total)")
                        continue
                    if received and time.monotonic() - last_data > 2.0:
                        break
                    time.sleep(0.05)
            else:
                while time.monotonic() < deadline:
                    try:
                        frame = client.read_frame(timeout=1.0)
                    except PerryNetTimeout:
                        continue
                    if frame.opcode == OP_TCP_DATA and frame.channel == channel:
                        received.extend(frame.payload)
                        print(f"tcp: received {len(frame.payload)} bytes ({len(received)} total)")
                        continue
                    if frame.opcode == OP_EVENT and frame.channel == channel and frame.payload:
                        event = frame.payload[0]
                        if event == EVT_TCP_CLOSED:
                            closed = True
                            break
                        if event == EVT_TCP_ERROR:
                            raise PerryNetError("TCP error event")

            client.tcp_close(channel)
            if not received:
                raise PerryNetTimeout("no TCP data received")

            first_line = received.splitlines()[0].decode("ascii", "replace")
            print(f"http: {first_line}")
            print(f"result: PASS ({len(received)} bytes, closed={int(closed)})")
    except PerryNetTimeout as exc:
        print(f"error: {exc}", file=sys.stderr)
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
