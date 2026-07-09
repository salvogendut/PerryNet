# Amstrad PCW Integration Notes

PerryNet is designed so the PCW does not need a full IP stack.  The ESP8266
owns WiFi and TCP/IP; the PCW driver only needs a serial frame layer plus a
small socket table.

## Recommended PCW Layers

1. BIOS or direct SIO serial driver.
2. SLIP byte stuffing and CRC-16/CCITT-FALSE.
3. PerryNet command dispatcher with sequence numbers.
4. CP/M BDOS-facing library, RSX, or application API.

A minimal CP/M library can expose calls like:

```text
pn_init()
pn_wifi_connect()
pn_dns(host, out_ip)
pn_tcp_open(host, port)
pn_tcp_send(channel, buffer, length)
pn_tcp_recv(channel, buffer, max_length)
pn_tcp_close(channel)
```

## Serial Handling

Use 8N1.  Start at 9600 baud until the PCW side has proven reliable.  Fresh
`d1_mini` builds default to RTS/CTS disabled so the Wemos can be configured
over USB serial immediately after flashing. Once installed in host hardware
that wires the flow-control pins, RTS/CTS can be enabled with the UART setting
command and saved to EEPROM.

The PerryFi board can use RTS/CTS:

- ESP8266 RTS tells the PCW whether the ESP8266 can receive more bytes.
- ESP8266 CTS is driven by the PCW to pause ESP8266-to-PCW output.

The firmware also supports a UART setting command.  On PerryFi hardware, some
requested baud rates are aliases for exact PCW PIT divisors: `19200` maps to
`17857` for the original PerryFi timing quirk, and `38400` maps to `41667`
for GEOBENCH Telnet's faster pull-mode profile.

## Receive Model

For fast hosts, the ESP8266 can send events and socket data at any time.  Those
hosts should keep one central receive pump:

```text
while serial byte available:
    feed SLIP decoder
    if frame complete and CRC valid:
        if opcode == ACK:
            complete pending command by sequence number
        if opcode == TCP_DATA:
            append to that channel receive queue
        if opcode == UDP_DATA:
            append datagram to that channel queue
        if opcode == EVENT:
            update socket/WiFi state
```

For memory-constrained or slow serial software, open TCP sockets with
`TCP_OPEN` flag bit 1 and use `TCP_RECV`.  In that mode the firmware suppresses
async `TCP_DATA` for that channel and only transmits network bytes as the ACK to
the host's receive request.  This avoids ESP8266-to-PCW serial output while the
PCW is repainting a window or polling the mouse.

## Why Not SLIP IP Packets?

SLIP/PPP would require an IP, TCP, UDP, DNS, and timer implementation on the
PCW.  That is possible, but heavy.  PerryNet instead acts like a hardware
socket controller.  This is closer to how many 8-bit Ethernet and WiFi modules
are used, and it is easier to share across CP/M applications.

## First PCW Milestone

A practical first host program should:

1. Send `HELLO` and print firmware details.
2. Configure WiFi if needed.
3. Resolve a host name.
4. Open a TCP socket to port 80.
5. Send a simple HTTP/1.0 request.
6. On PCW hardware, open TCP with pull RX enabled and poll `TCP_RECV` until
   `TCP_CLOSED`.  Async `TCP_DATA` remains available for hosts with larger
   serial buffers or working hardware flow control.

For boot clocks, prefer `TIME_GET` over opening UDP/NTP from GEOBENCH. PerryNet
starts SNTP after WiFi comes up; the PCW can read the valid UTC value later and
apply its local timezone offset without blocking the desktop.

That proves the serial link, DNS, TCP connect, TCP send, receive, and
close-event paths without requiring a resident driver.
