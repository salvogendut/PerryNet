#!/usr/bin/env python3
"""Reference PerryNet frame helpers.

This module intentionally has no serial dependency.  It is useful for tests and
for PC-side tools that want known-good SLIP and CRC behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

END = 0xC0
ESC = 0xDB
ESC_END = 0xDC
ESC_ESC = 0xDD
VERSION = 1


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def slip_encode(body: bytes) -> bytes:
    out = bytearray([END])
    for byte in body:
        if byte == END:
            out.extend((ESC, ESC_END))
        elif byte == ESC:
            out.extend((ESC, ESC_ESC))
        else:
            out.append(byte)
    out.append(END)
    return bytes(out)


def slip_feed(chunks: Iterable[bytes]) -> Iterable[bytes]:
    buf = bytearray()
    escaped = False
    for chunk in chunks:
        for byte in chunk:
            if byte == END:
                if buf:
                    yield bytes(buf)
                    buf.clear()
                escaped = False
                continue
            if escaped:
                if byte == ESC_END:
                    buf.append(END)
                elif byte == ESC_ESC:
                    buf.append(ESC)
                else:
                    buf.clear()
                escaped = False
                continue
            if byte == ESC:
                escaped = True
            else:
                buf.append(byte)


@dataclass(frozen=True)
class Frame:
    opcode: int
    seq: int
    channel: int
    payload: bytes = b""
    version: int = VERSION

    def body_without_crc(self) -> bytes:
        if len(self.payload) > 0xFFFF:
            raise ValueError("payload too large")
        return bytes(
            (
                self.version,
                self.opcode & 0xFF,
                self.seq & 0xFF,
                self.channel & 0xFF,
                len(self.payload) & 0xFF,
                (len(self.payload) >> 8) & 0xFF,
            )
        ) + self.payload

    def encode(self) -> bytes:
        body = self.body_without_crc()
        crc = crc16_ccitt_false(body)
        return slip_encode(body + crc.to_bytes(2, "little"))


def decode_body(body: bytes) -> Frame:
    if len(body) < 8:
        raise ValueError("frame too short")
    payload_len = body[4] | (body[5] << 8)
    expected_len = 6 + payload_len + 2
    if len(body) != expected_len:
        raise ValueError("bad frame length")
    crc_expected = int.from_bytes(body[-2:], "little")
    crc_actual = crc16_ccitt_false(body[:-2])
    if crc_expected != crc_actual:
        raise ValueError("bad crc")
    return Frame(
        version=body[0],
        opcode=body[1],
        seq=body[2],
        channel=body[3],
        payload=body[6:-2],
    )


def _selftest() -> None:
    frame = Frame(opcode=0x70, seq=3, channel=0, payload=bytes([0xC0, 0xDB, 1]))
    encoded = frame.encode()
    decoded = [decode_body(body) for body in slip_feed([encoded[:2], encoded[2:]])]
    assert decoded == [frame]
    assert crc16_ccitt_false(b"123456789") == 0x29B1


if __name__ == "__main__":
    _selftest()
    print("ok")

