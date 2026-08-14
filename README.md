# PerryNet

PerryNet is experimental firmware for the PerryFi ESP8266 device. Unlike the
original Hayes-style WiFi modem firmware, PerryNet exposes WiFi, DNS, TCP, UDP,
and a firmware-maintained NTP clock as a small framed socket API over the
existing serial link.

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
- read UTC time after the firmware has synced NTP
- receive asynchronous socket and WiFi status events

## Status

This repository currently contains the first firmware cut:

- ESP8266 Arduino firmware for Wemos D1 mini class PerryFi hardware and
  generic ESP-12F modules.
- SLIP-framed binary protocol over UART.
- WiFi credential storage in ESP8266 EEPROM emulation.
- TCP client sockets, TCP listeners with accept events, UDP sockets, DNS
  resolution, status events, and UART configuration.
- Automatic SNTP clock initialization after WiFi connects; hosts can poll
  `TIME_GET` instead of doing their own boot-time NTP exchange.
- Host-pulled TCP receive mode for 9600 baud PCW/DART hardware without RTS/CTS.
- Protocol documentation and PCW integration notes.
- Python reference helpers for host-side frame encoding/decoding.

The firmware is intentionally a socket service, not a SLIP/PPP network adapter.
That keeps the PCW side small enough for CP/M tools and makes the protocol
usable from other retro hosts without implementing a full TCP/IP stack there.

## Repository Layout

```text
.
|-- docs/
|   |-- flashing-and-wifi.md  flashing and first WiFi setup
|   |-- pcw-integration.md    PCW/CP-M driver notes
|   |-- perryfi-comparison.md comparison with original PerryFiFW
|   `-- protocol.md           PerryNet serial protocol reference
|-- src/
|   `-- main.cpp             ESP8266 firmware
|-- tools/
|   |-- perrynet.py          Python SLIP/CRC reference helper
|   |-- perrynet_serial.py   small host-side PerryNet serial client
|   |-- wifi_config.py       configure WiFi credentials over serial
|   |-- wifi_diag.py         detailed WiFi connection diagnostics
|   |-- internet_test.py     DNS/TCP/HTTP connectivity test
|   `-- uart_config.py       inspect/change UART settings
|-- platformio.ini           PlatformIO project configuration
`-- README.md
```

## Hardware

The firmware targets PerryFi-class Wemos D1 mini or ESP-12F wiring:

- ESP8266 UART0 TX/RX for host serial.
- `D8`/GPIO15 as UART RTS output.
- `D7`/GPIO13 as UART CTS input.
- Default serial mode: `9600 8N1`.

Fresh `d1_mini` and `esp12f` builds default to RTS/CTS disabled so a newly
flashed board can be configured over its USB serial bridge without a jumper.
Flow control can be enabled later with `tools/uart_config.py --rtscts --save`
for host hardware that wires the CTS/RTS pins.

There are also `esp01_1m` and `esp01` builds for ESP-01 modules. Those targets
disable PerryFi RTS/CTS support at compile time because a normal ESP-01 exposes
only UART TX/RX plus GPIO0/GPIO2.

## Protocol Summary

PerryNet uses SLIP-framed binary messages:

- every frame has a version, opcode, sequence number, channel, length, payload,
  and CRC-16/CCITT-FALSE
- host commands receive `ACK` frames with matching sequence numbers
- device events and socket data can be asynchronous; TCP sockets may also be
  opened in host-pulled receive mode
- TCP and UDP sockets are represented as small numeric channels

The full wire format is documented in [docs/protocol.md](docs/protocol.md).

## Building And Flashing

The default firmware target is `d1_mini` and uses the Arduino framework for
ESP8266. Use `esp12f` for a bare ESP-12F/4 MB module. ESP-01 modules should use
`esp01_1m` for 1 MB modules or `esp01` for older 512 KB modules.
See [docs/flashing-and-wifi.md](docs/flashing-and-wifi.md) for full build,
upload, first WiFi setup, and recovery instructions.

## First Host Test

A minimal host-side test should:

1. open the serial port at `9600 8N1`
2. send `HELLO`
3. configure WiFi with `WIFI_SET`
4. save settings with `SETTINGS_SAVE`
5. wait for boot autoconnect with `WIFI_STATUS`, or retry with `WIFI_CONNECT`
6. resolve a host with `DNS_RESOLVE`
7. open a TCP connection with `TCP_OPEN`
8. send bytes with `TCP_SEND`
9. optionally read firmware UTC with `TIME_GET`
10. either print incoming `TCP_DATA` frames or, on PCW-class serial links, open
    TCP with pull RX enabled and poll data with `TCP_RECV`

The Python helper in [tools/perrynet.py](tools/perrynet.py) is a reference for
SLIP escaping, CRC generation, and frame decoding. The setup and test tools use
[tools/perrynet_serial.py](tools/perrynet_serial.py) for host-side serial
commands.

## Configure WiFi From USB Serial

After flashing a Wemos D1 mini, configure WiFi through the PerryNet protocol
with `tools/wifi_config.py`, then verify connectivity with
`tools/internet_test.py`. The full command sequence is in
[docs/flashing-and-wifi.md](docs/flashing-and-wifi.md).

## Documentation

- [Flashing and first WiFi setup](docs/flashing-and-wifi.md)
- [Protocol reference](docs/protocol.md)
- [Amstrad PCW integration notes](docs/pcw-integration.md)
- [PerryNet vs original PerryFi firmware](docs/perryfi-comparison.md)

## Current Limitations

- The PCW/CP-M host driver is not implemented in this repository yet.
- TLS is not exposed; TCP sockets are plain TCP.
- The firmware is built for ESP8266/PerryFi-class hardware, not ESP32.
- The protocol is versioned but still experimental.

## Provenance

The PerryNet name is a tribute to Roland Perry, the Amstrad engineer associated
with the development of many of the company's computers.

This project was written for the PerryFi hardware and uses the original
PerryFi firmware as a hardware/protocol reference:

<https://github.com/SanPollo/PerryFiFW>

The code in this repository is new implementation work.  The license is
GPL-3.0-or-later to stay compatible with the PerryFi firmware ecosystem.
