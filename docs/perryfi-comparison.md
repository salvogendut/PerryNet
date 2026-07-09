# PerryNet vs PerryFiFW

PerryNet targets PerryFi-class ESP8266 hardware, but it is not a drop-in
replacement for the original PerryFi firmware. It is a different firmware
personality for hosts that want structured network services instead of an
interactive modem.

The original PerryFi firmware is based on RetroWiFiModem and presents a
Hayes-style text interface. The host sends `AT` commands, configures SSID and
password, dials a TCP endpoint with commands such as `ATDT host:port`, receives
modem result codes such as `OK`, `CONNECT`, `RING`, `NO CARRIER`, and then
streams bytes through the active connection.

PerryNet presents a framed socket API. The host sends SLIP-framed binary
commands with sequence numbers and CRCs, and receives structured `ACK`, event,
TCP data, and UDP data frames. This is less convenient for a human terminal,
but it is much easier for a desktop, CP/M program, or resident driver to use
without parsing modem text.

## Main Differences

| Area | Original PerryFiFW | PerryNet |
| --- | --- | --- |
| Host protocol | Hayes-style text `AT` commands | SLIP-framed binary protocol with CRC |
| Primary model | WiFi modem: dial one endpoint and stream bytes | TCP/IP offload service with explicit commands |
| TCP client | `ATDT host:port`, then transparent serial bridge | `DNS_RESOLVE`, `TCP_OPEN`, `TCP_SEND`, `TCP_RECV` or async `TCP_DATA` |
| TCP server | Modem-style listen/auto-answer behavior | Explicit listener channels and `TCP_ACCEPT` events |
| UDP | Not exposed as a general host API | `UDP_OPEN`, `UDP_SEND`, async `UDP_DATA` |
| Time | Host-side programs must use modem/network facilities | Firmware SNTP clock exposed through `TIME_GET` |
| Configuration | AT commands and a modem-style profile | Binary `WIFI_SET`, `SETTINGS_SAVE`, `UART_SET` commands |
| Best fit | Terminal software and BBS/MUD-style use | Operating environments and applications needing sockets |

## Why GEOBENCH Uses PerryNet

GEOBENCH needs small, predictable operations: check WiFi status, resolve DNS,
open sockets, send or receive bounded chunks, and set the desktop clock. Doing
that through a modem command stream would require more parsing, more state, and
more resident code on the PCW side.

PerryNet keeps TCP/IP, DNS, UDP, and SNTP on the ESP8266. The PCW side only
needs a serial frame layer plus a small command wrapper.

## PCW-Specific Choices

PerryNet keeps the original PerryFi hardware assumptions where they matter:

- Wemos D1 mini / PerryFi-class ESP8266 hardware.
- `D8` / GPIO15 as UART RTS output and `D7` / GPIO13 as UART CTS input.
- Default `9600 8N1`.
- PCW-oriented nominal baud settings map internally to exact CPS8256 divisors:
  `19200` -> `17857`, matching PerryFiFW's timing quirk, and `38400` ->
  `41667` for GEOBENCH Telnet's faster pull-mode profile.

PerryNet also adds behavior specifically useful on PCW-class serial hardware:

- Fresh builds start with RTS/CTS disabled so a bare Wemos can be configured
  over USB serial without a jumper.
- TCP sockets can be opened in host-pulled receive mode, suppressing async
  `TCP_DATA` and letting the PCW poll with `TCP_RECV`.
- The firmware maintains SNTP time in the background; GEOBENCH can use
  `TIME_GET` instead of doing its own boot-time UDP/NTP exchange.

## Compatibility

Software that expects PerryFiFW `AT` commands will not work unchanged with
PerryNet. Software written for PerryNet will not work against PerryFiFW.

Use PerryFiFW when you want a retro WiFi modem. Use PerryNet when the host
software wants to treat the ESP8266 as a small socket controller.

## References

- Original PerryFi firmware: <https://github.com/SanPollo/PerryFiFW>
- PerryNet protocol: [protocol.md](protocol.md)
- PCW integration notes: [pcw-integration.md](pcw-integration.md)
