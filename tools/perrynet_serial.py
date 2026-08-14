#!/usr/bin/env python3
"""Small PerryNet serial client used by local setup/test tools."""

from __future__ import annotations

import time
from dataclasses import dataclass

import serial

from perrynet import END, ESC, ESC_END, ESC_ESC, Frame, decode_body

OP_HELLO = 0x01
OP_WIFI_GET = 0x10
OP_WIFI_SET = 0x11
OP_WIFI_CONNECT = 0x12
OP_WIFI_STATUS = 0x14
OP_SETTINGS_SAVE = 0x15
OP_WIFI_DIAG = 0x16
OP_DNS_RESOLVE = 0x20
OP_TCP_OPEN = 0x30
OP_TCP_CLOSE = 0x31
OP_TCP_SEND = 0x32
OP_TCP_RECV = 0x35
OP_UART_GET = 0x50
OP_UART_SET = 0x51
OP_TIME_GET = 0x60
OP_ACK = 0x80
OP_EVENT = 0x81
OP_TCP_DATA = 0x82

EVT_WIFI_UP = 0x02
EVT_WIFI_DOWN = 0x03
EVT_TCP_CLOSED = 0x11
EVT_TCP_ERROR = 0x12

STATUS_NAMES = {
    0x00: "OK",
    0x01: "BAD_FRAME",
    0x02: "BAD_OPCODE",
    0x03: "BAD_LENGTH",
    0x04: "BAD_CHANNEL",
    0x05: "NO_SLOT",
    0x06: "WIFI_DOWN",
    0x07: "CONNECT_FAILED",
    0x08: "IO_ERROR",
    0x09: "UNSUPPORTED",
    0x0A: "BUSY",
    0x0B: "BAD_ARGUMENT",
}

WIFI_STATUS_NAMES = {
    0: "IDLE",
    1: "NO_SSID_AVAIL",
    2: "SCAN_COMPLETED",
    3: "CONNECTED",
    4: "CONNECT_FAILED",
    5: "CONNECTION_LOST",
    6: "WRONG_PASSWORD",
    7: "DISCONNECTED",
}


class PerryNetError(Exception):
    pass


class PerryNetTimeout(PerryNetError):
    pass


class PerryNetCommandError(PerryNetError):
    def __init__(self, status: int):
        self.status = status
        super().__init__(STATUS_NAMES.get(status, f"status 0x{status:02X}"))


@dataclass
class WifiStatus:
    raw_status: int
    connected: bool
    rssi: int
    ip: str
    gateway: str
    netmask: str
    dns: str
    mac: str

    @property
    def status_name(self) -> str:
        return WIFI_STATUS_NAMES.get(self.raw_status, f"0x{self.raw_status:02X}")


@dataclass
class TimeStatus:
    valid: bool
    unix_utc: int
    uptime_ms: int


def ip4(data: bytes) -> str:
    return ".".join(str(b) for b in data)


def parse_wifi_status(payload: bytes) -> WifiStatus:
    if len(payload) < 28:
        raise PerryNetError(f"short WIFI_STATUS response: {len(payload)} bytes")
    rssi = int.from_bytes(payload[2:6], "little", signed=True)
    return WifiStatus(
        raw_status=payload[0],
        connected=payload[1] != 0,
        rssi=rssi,
        ip=ip4(payload[6:10]),
        gateway=ip4(payload[10:14]),
        netmask=ip4(payload[14:18]),
        dns=ip4(payload[18:22]),
        mac=":".join(f"{b:02x}" for b in payload[22:28]),
    )


class PerryNetClient:
    def __init__(self, port: str, baud: int = 9600, read_timeout: float = 0.05):
        self.serial = serial.Serial(port, baudrate=baud, timeout=read_timeout)
        # Wemos/NodeMCU-style ESP8266 boards wire DTR/RTS to reset and GPIO0
        # for auto-upload. Normal PerryNet protocol clients should release
        # those lines so opening the port does not hold the board in reset or
        # bootloader mode, then wait for the application to finish booting.
        self.serial.dtr = False
        self.serial.rts = False
        time.sleep(1.0)
        self.seq = 0
        self._rx = bytearray()
        self._escaped = False
        self._pending: list[Frame] = []

    def close(self) -> None:
        self.serial.close()

    def __enter__(self) -> "PerryNetClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def drain(self, seconds: float = 0.25) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.serial.in_waiting:
                self.serial.read(self.serial.in_waiting)
            else:
                time.sleep(0.01)
        self._rx.clear()
        self._escaped = False
        self._pending.clear()

    def next_seq(self) -> int:
        self.seq = (self.seq % 255) + 1
        return self.seq

    def send(self, opcode: int, payload: bytes = b"", channel: int = 0) -> int:
        seq = self.next_seq()
        self.serial.write(Frame(opcode=opcode, seq=seq, channel=channel, payload=payload).encode())
        self.serial.flush()
        return seq

    def read_frame(self, timeout: float = 2.0) -> Frame:
        if self._pending:
            return self._pending.pop(0)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = self.serial.read(max(1, self.serial.in_waiting))
            if not chunk:
                continue
            for byte in chunk:
                frame = self._feed_byte(byte)
                if frame is not None:
                    return frame
        raise PerryNetTimeout("timed out waiting for frame")

    def _feed_byte(self, byte: int) -> Frame | None:
        if byte == END:
            if not self._rx:
                self._escaped = False
                return None
            body = bytes(self._rx)
            self._rx.clear()
            self._escaped = False
            try:
                return decode_body(body)
            except ValueError:
                return None
        if self._escaped:
            if byte == ESC_END:
                self._rx.append(END)
            elif byte == ESC_ESC:
                self._rx.append(ESC)
            else:
                self._rx.clear()
            self._escaped = False
            return None
        if byte == ESC:
            self._escaped = True
        else:
            self._rx.append(byte)
        return None

    def command(self, opcode: int, payload: bytes = b"", channel: int = 0,
                timeout: float = 5.0) -> bytes:
        seq = self.send(opcode, payload, channel)
        deadline = time.monotonic() + timeout
        skipped: list[Frame] = []
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise PerryNetTimeout(f"timed out waiting for ACK 0x{opcode:02X}")
                frame = self.read_frame(remaining)
                if frame.opcode != OP_ACK or frame.seq != seq:
                    skipped.append(frame)
                    continue
                if not frame.payload:
                    raise PerryNetError("empty ACK payload")
                status = frame.payload[0]
                if status:
                    raise PerryNetCommandError(status)
                return frame.payload[1:]
        finally:
            if skipped:
                self._pending = skipped + self._pending

    def hello(self) -> tuple[int, int, int, int, int, int, str]:
        payload = self.command(OP_HELLO)
        if len(payload) < 10:
            raise PerryNetError("short HELLO response")
        name = payload[10:].split(b"\0", 1)[0].decode("ascii", "replace")
        return (
            payload[0],
            payload[1],
            int.from_bytes(payload[2:4], "little"),
            payload[4],
            payload[5],
            int.from_bytes(payload[6:10], "little"),
            name,
        )

    def wifi_status(self, timeout: float = 5.0) -> WifiStatus:
        return parse_wifi_status(self.command(OP_WIFI_STATUS, timeout=timeout))

    def wifi_get(self) -> tuple[str, bool]:
        payload = self.command(OP_WIFI_GET)
        if len(payload) < 2:
            raise PerryNetError("short WIFI_GET response")
        ssid_len = payload[0]
        if len(payload) != 2 + ssid_len:
            raise PerryNetError("bad WIFI_GET response length")
        ssid = payload[2:].decode("utf-8", "replace")
        return ssid, payload[1] != 0

    def wait_wifi(self, timeout: float = 30.0) -> WifiStatus:
        deadline = time.monotonic() + timeout
        last: WifiStatus | None = None
        last_timeout: PerryNetTimeout | None = None
        while time.monotonic() < deadline:
            time.sleep(1.0)
            try:
                remaining = max(1.0, min(10.0, deadline - time.monotonic()))
                last = self.wifi_status(timeout=remaining)
                last_timeout = None
            except PerryNetTimeout as exc:
                last_timeout = exc
                continue
            if last.connected:
                return last
        if last is not None:
            raise PerryNetTimeout(f"WiFi did not connect, last status: {last.status_name}")
        if last_timeout is not None:
            raise PerryNetTimeout(f"WiFi status did not respond: {last_timeout}")
        raise PerryNetTimeout("WiFi did not connect")

    def set_wifi(self, ssid: str, password: str) -> None:
        ssid_b = ssid.encode("utf-8")
        pass_b = password.encode("utf-8")
        if len(ssid_b) > 32:
            raise PerryNetError("SSID is longer than 32 bytes")
        if len(pass_b) > 64:
            raise PerryNetError("password is longer than 64 bytes")
        self.command(OP_WIFI_SET, bytes((len(ssid_b), len(pass_b))) + ssid_b + pass_b)

    def dns_resolve(self, host: str) -> str:
        payload = self.command(OP_DNS_RESOLVE, host.encode("ascii"))
        if len(payload) != 4:
            raise PerryNetError("short DNS response")
        return ip4(payload)

    def time_get(self) -> TimeStatus:
        payload = self.command(OP_TIME_GET)
        if len(payload) < 9:
            raise PerryNetError("short TIME_GET response")
        return TimeStatus(
            bool(payload[0]),
            int.from_bytes(payload[1:5], "little"),
            int.from_bytes(payload[5:9], "little"),
        )

    def tcp_open(self, host: str, port: int, nodelay: bool = True, pull_rx: bool = False) -> int:
        host_b = host.encode("ascii")
        if len(host_b) > 253:
            raise PerryNetError("host name too long")
        flags = (1 if nodelay else 0) | (2 if pull_rx else 0)
        payload = bytes((len(host_b),)) + host_b + port.to_bytes(2, "little") + bytes((flags,))
        response = self.command(OP_TCP_OPEN, payload, timeout=10.0)
        if len(response) < 7:
            raise PerryNetError("short TCP_OPEN response")
        return response[0]

    def tcp_send(self, channel: int, data: bytes) -> int:
        response = self.command(OP_TCP_SEND, data, channel=channel)
        if len(response) < 2:
            return 0
        return int.from_bytes(response[:2], "little")

    def tcp_recv(self, channel: int, max_len: int = 64) -> bytes:
        if max_len < 0:
            max_len = 0
        if max_len > 511:
            max_len = 511
        return self.command(OP_TCP_RECV, max_len.to_bytes(2, "little"), channel=channel, timeout=2.0)

    def tcp_close(self, channel: int) -> None:
        try:
            self.command(OP_TCP_CLOSE, channel=channel, timeout=2.0)
        except PerryNetError:
            pass
