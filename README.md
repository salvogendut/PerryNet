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

- ESP8266 Arduino firmware for Wemos D1 mini class PerryFi hardware.
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
|   |-- pcw-integration.md   PCW/CP-M driver notes
|   `-- protocol.md          PerryNet serial protocol reference
|-- src/
|   `-- main.cpp             ESP8266 firmware
|-- tools/
|   |-- perrynet.py          Python SLIP/CRC reference helper
|   |-- perrynet_serial.py   small host-side PerryNet serial client
|   |-- wifi_config.py       configure WiFi credentials over serial
|   |-- internet_test.py     DNS/TCP/HTTP connectivity test
|   `-- uart_config.py       inspect/change UART settings
|-- platformio.ini           PlatformIO project configuration
`-- README.md
```

## Hardware

The firmware targets PerryFi-class Wemos D1 mini wiring:

- ESP8266 UART0 TX/RX for host serial.
- `D8`/GPIO15 as UART RTS output.
- `D7`/GPIO13 as UART CTS input.
- Default serial mode: `9600 8N1`.

Fresh `d1_mini` builds default to RTS/CTS disabled so a newly flashed Wemos can
be configured over its USB serial bridge without a jumper. Flow control can be
enabled later with `tools/uart_config.py --rtscts --save` for host hardware that
wires the CTS/RTS pins.

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

After flashing a Wemos D1 mini, configure WiFi through the PerryNet protocol:

```sh
SSID='your wifi' PASS='your password' PORT=/dev/ttyUSB0 \
  .venv/bin/python tools/wifi_config.py
```

This sends `WIFI_SET`, saves the credentials with `SETTINGS_SAVE`, then sends
`WIFI_CONNECT` to verify them immediately and prints the assigned IP address.
The firmware auto-connects on later boots once credentials are saved.

To verify that the configured device can reach the internet:

```sh
PORT=/dev/ttyUSB0 .venv/bin/python tools/internet_test.py
```

The internet test checks WiFi status, resolves `example.com`, opens a TCP
connection to port 80, sends an HTTP request, and reports the returned status
line.

If the tool times out waiting for a frame on a bare Wemos D1 mini, the board may
already have older saved settings with UART RTS/CTS enabled. Normal firmware
uploads can preserve the ESP8266 EEPROM-emulation sector. Temporarily connect
`D7` / GPIO13 to `GND`, reset the board, then save flow control disabled:

```sh
PORT=/dev/ttyUSB0 .venv/bin/python tools/uart_config.py --no-rtscts --save
```

Remove the temporary `D7` to `GND` connection, reset again, then rerun
`wifi_config.py`.

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
