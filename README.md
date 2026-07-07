# PerryNet

PerryNet is experimental firmware for the PerryFi ESP8266 device. Unlike the
original Hayes-style WiFi modem firmware, PerryNet exposes WiFi, DNS, TCP, and
UDP as a small framed socket API over the existing serial link.

The first target is the Amstrad PCW/PerryFi board, but the protocol is host
neutral. Anything that can speak 8-bit serial can use the ESP8266 as a TCP/IP
offload device.

## Why This Exists

The Amstrad PCW can talk to the PerryFi board over serial, but running a full
TCP/IP stack on the PCW side is expensive in memory, CPU time, and driver
complexity. PerryNet keeps the TCP/IP stack on the ESP8266 and gives the host a
compact socket API instead.

That means a CP/M program or resident driver can do practical network work with
small operations:

- configure and connect WiFi
- resolve DNS names
- open TCP client connections
- accept inbound TCP connections
- send and receive UDP datagrams
- receive asynchronous socket and WiFi status events

## Status

This repository currently contains the first firmware cut:

- ESP8266 Arduino firmware for Wemos D1 mini class PerryFi hardware.
- SLIP-framed binary protocol over UART.
- WiFi credential storage in ESP8266 EEPROM emulation.
- TCP client sockets, TCP listeners with accept events, UDP sockets, DNS
  resolution, status events, and UART configuration.
- Protocol documentation and PCW integration notes.
- Python reference helpers for host-side frame encoding/decoding.

The firmware is intentionally a socket service, not a SLIP/PPP network adapter.
That keeps the PCW side small enough for CP/M tools and makes the protocol
usable from other retro hosts without implementing a full TCP/IP stack there.

## Repository Layout

```text
.
|-- docs/
|   |-- pcw-integration.md   PCW/CP-M driver notes
|   `-- protocol.md          PerryNet serial protocol reference
|-- src/
|   `-- main.cpp             ESP8266 firmware
|-- tools/
|   `-- perrynet.py          Python SLIP/CRC reference helper
|-- platformio.ini           PlatformIO project configuration
`-- README.md
```

## Hardware

The defaults match the PerryFi firmware's Wemos D1 mini wiring:

- ESP8266 UART0 TX/RX for host serial.
- `D8`/GPIO15 as UART RTS output.
- `D7`/GPIO13 as UART CTS input.
- Default serial mode: `9600 8N1`.

RTS/CTS is enabled by default in the stored settings because native socket
traffic can burst faster than the PCW can consume it.  It can be disabled with
the protocol if required.

## Protocol Summary

PerryNet uses SLIP-framed binary messages:

- every frame has a version, opcode, sequence number, channel, length, payload,
  and CRC-16/CCITT-FALSE
- host commands receive `ACK` frames with matching sequence numbers
- device events and socket data are asynchronous
- TCP and UDP sockets are represented as small numeric channels

The full wire format is documented in [docs/protocol.md](docs/protocol.md).

## Building And Flashing

With PlatformIO:

```sh
pio run
pio run -t upload
```

The firmware target is `d1_mini` and uses the Arduino framework for ESP8266.

If PlatformIO is not installed globally, a local install works too:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install platformio
env PLATFORMIO_CORE_DIR=.platformio .venv/bin/platformio run
```

The generated firmware binary is:

```text
.pio/build/d1_mini/firmware.bin
```

## First Host Test

A minimal host-side test should:

1. open the serial port at `9600 8N1`
2. send `HELLO`
3. configure WiFi with `WIFI_SET`
4. save settings with `SETTINGS_SAVE`
5. connect with `WIFI_CONNECT`
6. resolve a host with `DNS_RESOLVE`
7. open a TCP connection with `TCP_OPEN`
8. send bytes with `TCP_SEND`
9. print incoming `TCP_DATA` frames until `TCP_CLOSED`

The Python helper in [tools/perrynet.py](tools/perrynet.py) is a reference for
SLIP escaping, CRC generation, and frame decoding. It is not a complete serial
client yet.

## Documentation

- [Protocol reference](docs/protocol.md)
- [Amstrad PCW integration notes](docs/pcw-integration.md)

## Current Limitations

- The PCW/CP-M host driver is not implemented in this repository yet.
- TLS is not exposed; TCP sockets are plain TCP.
- The firmware is built for ESP8266/PerryFi-class hardware, not ESP32.
- The protocol is versioned but still experimental.

## Provenance

This project was written for the PerryFi hardware and uses the original
PerryFi firmware as a hardware/protocol reference:

<https://github.com/SanPollo/PerryFiFW>

The code in this repository is new implementation work.  The license is
GPL-3.0-or-later to stay compatible with the PerryFi firmware ecosystem.
