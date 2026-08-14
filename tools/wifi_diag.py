#!/usr/bin/env python3
"""Show detailed PerryNet WiFi diagnostics."""

from __future__ import annotations

import argparse
import os
import sys

from perrynet_serial import (
    OP_WIFI_DIAG,
    WIFI_STATUS_NAMES,
    PerryNetClient,
    PerryNetError,
    PerryNetTimeout,
    ip4,
)


REASON_NAMES = {
    0: "NONE",
    1: "UNSPECIFIED",
    2: "AUTH_EXPIRE",
    3: "AUTH_LEAVE",
    4: "ASSOC_EXPIRE",
    5: "ASSOC_TOOMANY",
    6: "NOT_AUTHED",
    7: "NOT_ASSOCED",
    8: "ASSOC_LEAVE",
    9: "ASSOC_NOT_AUTHED",
    10: "DISASSOC_PWRCAP_BAD",
    11: "DISASSOC_SUPCHAN_BAD",
    13: "IE_INVALID",
    14: "MIC_FAILURE",
    15: "4WAY_HANDSHAKE_TIMEOUT",
    16: "GROUP_KEY_UPDATE_TIMEOUT",
    17: "IE_IN_4WAY_DIFFERS",
    18: "GROUP_CIPHER_INVALID",
    19: "PAIRWISE_CIPHER_INVALID",
    20: "AKMP_INVALID",
    21: "UNSUPP_RSN_IE_VERSION",
    22: "INVALID_RSN_IE_CAP",
    23: "802_1X_AUTH_FAILED",
    24: "CIPHER_SUITE_REJECTED",
    200: "BEACON_TIMEOUT",
    201: "NO_AP_FOUND",
    202: "AUTH_FAIL",
    203: "ASSOC_FAIL",
    204: "HANDSHAKE_TIMEOUT",
}

MODE_NAMES = {
    0: "OFF",
    1: "STA",
    2: "AP",
    3: "AP_STA",
}

PHY_NAMES = {
    1: "11B",
    2: "11G",
    3: "11N",
}

SLEEP_NAMES = {
    0: "NONE",
    1: "LIGHT",
    2: "MODEM",
}


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", default=os.environ.get("PORT", "/dev/ttyUSB0"),
                    help="serial port, default: PORT env or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=int(os.environ.get("BAUD", "9600")),
                    help="serial baud rate, default: BAUD env or 9600")
    return ap


def u32(payload: bytes, offset: int) -> int:
    return int.from_bytes(payload[offset:offset + 4], "little")


def u16(payload: bytes, offset: int) -> int:
    return int.from_bytes(payload[offset:offset + 2], "little")


def mac(data: bytes) -> str:
    return ":".join(f"{b:02x}" for b in data)


def main() -> int:
    args = parser().parse_args()
    try:
        with PerryNetClient(args.port, args.baud) as client:
            client.drain()
            major, minor, *_rest, name = client.hello()
            print(f"device: {name} v{major}.{minor}")
            payload = client.command(OP_WIFI_DIAG, timeout=10.0)
            if len(payload) < 77:
                raise PerryNetError(f"short WIFI_DIAG response: {len(payload)} bytes")

            reason = u16(payload, 38)
            print(f"wifi: status={WIFI_STATUS_NAMES.get(payload[0], payload[0])} "
                  f"connected={payload[1]} mode={MODE_NAMES.get(payload[2], payload[2])} "
                  f"phy={PHY_NAMES.get(payload[3], payload[3])} "
                  f"sleep={SLEEP_NAMES.get(payload[4], payload[4])} channel={payload[5]}")
            print(f"signal: rssi={int.from_bytes(payload[6:10], 'little', signed=True)}dBm "
                  f"last_channel={payload[76]}")
            print(f"ip: local={ip4(payload[10:14])} gateway={ip4(payload[14:18])} "
                  f"netmask={ip4(payload[18:22])} dns={ip4(payload[22:26])}")
            print(f"mac: sta={mac(payload[26:32])} bssid={mac(payload[32:38])}")
            print(f"disconnect: reason={reason} {REASON_NAMES.get(reason, 'UNKNOWN')}")
            print(f"events: attempts={u32(payload, 40)} connected={u32(payload, 44)} "
                  f"disconnected={u32(payload, 48)} got_ip={u32(payload, 52)} "
                  f"dhcp_timeout={u32(payload, 56)}")
            print(f"age_ms: last_attempt={u32(payload, 60)} connected={u32(payload, 64)} "
                  f"disconnected={u32(payload, 68)} got_ip={u32(payload, 72)}")
    except PerryNetTimeout as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, PerryNetError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
