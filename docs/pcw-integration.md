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

Use 8N1.  Start at 9600 baud until the PCW side has proven reliable.  The
PerryFi board can use RTS/CTS:

- ESP8266 RTS tells the PCW whether the ESP8266 can receive more bytes.
- ESP8266 CTS is driven by the PCW to pause ESP8266-to-PCW output.

The firmware also supports a UART setting command.  On PerryFi hardware, a
requested 19200 baud is internally mapped to 17857 baud to match the original
PCW WiFi board timing quirk documented by the PerryFi firmware.

## Receive Model

The ESP8266 can send events and socket data at any time.  PCW software should
keep one central receive pump:

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

For memory-constrained software, use small per-channel ring buffers and request
application reads frequently.  The firmware chunks incoming network data into
512-byte maximum frames, but the PCW side may choose smaller buffers.

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
6. Print `TCP_DATA` frames until `TCP_CLOSED`.

That proves the serial link, DNS, TCP connect, TCP send, async receive, and
close-event paths without requiring a resident driver.

