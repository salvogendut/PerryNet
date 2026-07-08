# PerryNet Serial Protocol

PerryNet exposes the ESP8266 WiFi stack as a serial socket service.  The host
does not send IP packets.  Instead it opens TCP/UDP channels and exchanges byte
streams or datagrams.

## Link Layer

Serial defaults:

- 9600 baud
- 8 data bits
- no parity
- 1 stop bit
- RTS/CTS disabled in fresh `d1_mini` builds, so USB serial setup works on a
  bare Wemos D1 mini

Each frame is SLIP encoded:

| Byte | Meaning |
| --- | --- |
| `C0` | frame boundary |
| `DB DC` | escaped `C0` data byte |
| `DB DD` | escaped `DB` data byte |

The unescaped frame body is:

| Offset | Size | Field |
| --- | ---: | --- |
| 0 | 1 | protocol version, currently `1` |
| 1 | 1 | opcode |
| 2 | 1 | sequence number |
| 3 | 1 | channel |
| 4 | 2 | payload length, little endian |
| 6 | n | payload |
| 6+n | 2 | CRC-16/CCITT-FALSE over all prior body bytes |

Maximum payload is 512 bytes.  Hosts should keep TCP writes at or below the
advertised maximum returned by `HELLO`.

## Responses

Most host commands receive an `ACK` frame with the same sequence number.

`ACK` payload:

| Offset | Size | Field |
| --- | ---: | --- |
| 0 | 1 | status |
| 1 | n | command-specific response |

Status values:

| Value | Name | Meaning |
| ---: | --- | --- |
| `00` | OK | command accepted |
| `01` | BAD_FRAME | malformed frame or CRC failure |
| `02` | BAD_OPCODE | unknown opcode |
| `03` | BAD_LENGTH | payload length is invalid for opcode |
| `04` | BAD_CHANNEL | channel does not exist or has wrong type |
| `05` | NO_SLOT | no free socket/listener slot |
| `06` | WIFI_DOWN | WiFi is not connected |
| `07` | CONNECT_FAILED | TCP/DNS/WiFi operation failed |
| `08` | IO_ERROR | socket read/write failed |
| `09` | UNSUPPORTED | option is not supported |
| `0A` | BUSY | operation cannot be completed now |
| `0B` | BAD_ARGUMENT | argument is invalid |

Async device frames use sequence `0`.

## Opcodes

| Opcode | Name | Direction | Channel | Payload |
| ---: | --- | --- | --- | --- |
| `01` | HELLO | host to device | 0 | optional |
| `02` | RESET_DEVICE | host to device | 0 | empty |
| `10` | WIFI_GET | host to device | 0 | empty |
| `11` | WIFI_SET | host to device | 0 | `ssid_len, pass_len, ssid, pass` |
| `12` | WIFI_CONNECT | host to device | 0 | empty |
| `13` | WIFI_DISCONNECT | host to device | 0 | empty |
| `14` | WIFI_STATUS | host to device | 0 | empty |
| `15` | SETTINGS_SAVE | host to device | 0 | empty |
| `20` | DNS_RESOLVE | host to device | 0 | host name bytes |
| `30` | TCP_OPEN | host to device | 0 | `host_len, host, port_le16, flags` |
| `31` | TCP_CLOSE | host to device | TCP channel | empty |
| `32` | TCP_SEND | host to device | TCP channel | bytes |
| `33` | TCP_LISTEN | host to device | 0 | `port_le16` |
| `34` | TCP_LISTEN_CLOSE | host to device | listener channel | empty |
| `35` | TCP_RECV | host to device | TCP channel | optional `max_len_le16` |
| `40` | UDP_OPEN | host to device | 0 | `local_port_le16` |
| `41` | UDP_CLOSE | host to device | UDP channel | empty |
| `42` | UDP_SEND | host to device | UDP channel | `ip4, port_le16, bytes` |
| `50` | UART_GET | host to device | 0 | empty |
| `51` | UART_SET | host to device | 0 | `baud_le32, flags` |
| `60` | TIME_GET | host to device | 0 | empty |
| `70` | PING | host to device | 0 | arbitrary bytes |
| `80` | ACK | device to host | copied | `status, response...` |
| `81` | EVENT | device to host | channel | `event, detail...` |
| `82` | TCP_DATA | device to host | TCP channel | bytes |
| `83` | UDP_DATA | device to host | UDP channel | `ip4, port_le16, bytes` |

`TCP_OPEN` flags:

| Bit | Meaning |
| ---: | --- |
| 0 | disable Nagle (`TCP_NODELAY`) |
| 1 | host-pulled receive; suppress async `TCP_DATA`, use `TCP_RECV` |

`UART_SET` flags:

| Bit | Meaning |
| ---: | --- |
| 0 | enable RTS/CTS |
| 1 | save UART settings to EEPROM |

The UART setting change is applied after the `ACK` frame has been transmitted.
The host side must switch baud rate immediately after receiving that ACK.

Host hardware that wires ESP8266 `D8`/GPIO15 and `D7`/GPIO13 for RTS/CTS can
enable flow control with `UART_SET`; direct USB serial setup should leave it
disabled.

Current PCW/PerryFi builds force `9600 8N1` with RTS/CTS disabled at boot so
older saved EEPROM UART settings cannot leave the Wemos silent on the PCW.

## Command Response Payloads

`HELLO` response:

```text
u8  major_version
u8  minor_version
u16 max_payload_le
u8  max_channels
u8  max_listeners
u32 feature_flags_le
char firmware_name_nul
```

Feature flags:

| Bit | Meaning |
| ---: | --- |
| 0 | WiFi station |
| 1 | DNS |
| 2 | TCP client |
| 3 | TCP listener |
| 4 | UDP |
| 5 | UART settings |

`WIFI_GET` response:

```text
u8 ssid_len
u8 pass_is_set
ssid bytes
```

The password is never returned.

`WIFI_STATUS` response:

```text
u8 wifi_status
u8 connected
i32 rssi_le
u8 ip4[4]
u8 gateway[4]
u8 netmask[4]
u8 dns[4]
u8 mac[6]
```

`DNS_RESOLVE` response:

```text
u8 ip4[4]
```

`TCP_OPEN` response:

```text
u8 tcp_channel
u8 local_ip4[4]
u16 local_port_le
```

`TCP_LISTEN` response:

```text
u8 listener_channel
u16 port_le
```

`TCP_RECV` response:

```text
u8 bytes[0..max_len]
```

If `max_len` is omitted, the firmware uses its default TCP read chunk. The
response may be empty when no data is ready. Hosts with small serial FIFOs, such
as the PCW/PerryFi path, should open TCP sockets with `TCP_OPEN` flag bit 1 and
pull data explicitly with `TCP_RECV` so the firmware does not transmit while the
host is repainting or polling other devices.

`UDP_OPEN` response:

```text
u8 udp_channel
u16 local_port_le
```

`UART_GET` response:

```text
u32 baud_le
u8 flags
```

`TIME_GET` response:

```text
u8  valid
u32 unix_utc_le
u32 uptime_ms_le
```

`valid` is `1` after the firmware has obtained NTP time. The firmware starts
SNTP automatically when WiFi comes up and retries in the background; hosts
should not block boot waiting for it.

## Events

| Event | Name | Channel | Detail |
| ---: | --- | --- | --- |
| `01` | READY | 0 | empty |
| `02` | WIFI_UP | 0 | same network fields as `WIFI_STATUS` after `connected` |
| `03` | WIFI_DOWN | 0 | empty |
| `10` | TCP_ACCEPT | TCP channel | `listener_channel, remote_ip4, remote_port_le` |
| `11` | TCP_CLOSED | TCP channel | empty |
| `12` | TCP_ERROR | TCP channel | status |
| `20` | UDP_ERROR | UDP channel | status |

`READY` is sent once at boot as a presence event. Hosts should treat it as
asynchronous and ignore it while waiting for command ACKs.

## Suggested Host Flow

1. Send `HELLO`.
2. Configure WiFi once with `WIFI_SET`, then `SETTINGS_SAVE`.
3. On later boots, wait for `WIFI_UP` or poll `WIFI_STATUS`; saved credentials
   automatically start a connection attempt at boot.
4. Send `WIFI_CONNECT` only to retry or to force a connection attempt; `ACK OK`
   means the attempt started, not that WiFi is already connected.
5. Use `TIME_GET` when a host-side clock is needed; apply any local timezone
   offset on the host.
6. Use `DNS_RESOLVE`, then open one or more TCP/UDP sockets.
7. For normal TCP sockets, treat `TCP_DATA`, `UDP_DATA`, and `EVENT` frames as
   asynchronous input. For pull-mode TCP sockets, poll with `TCP_RECV` instead.

The host should use sequence numbers for commands and ignore sequence `0` for
command matching.
