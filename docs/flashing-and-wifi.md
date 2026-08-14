# Flashing And First WiFi Setup

These steps cover PerryFi-class Wemos D1 mini boards, generic ESP-12F modules,
and ESP-01 modules connected to the host over USB serial.

## Prerequisites

- PlatformIO, either installed globally as `pio` or in a local Python virtual
  environment.
- Python `pyserial` for the setup/test tools.
- A serial device such as `/dev/ttyUSB0` or `/dev/ttyACM0`.
- WiFi SSID and password for a 2.4 GHz network supported by the ESP8266.

The default firmware target is `d1_mini`. Use `esp12f` for a bare ESP-12F/4 MB
module. Fresh builds default to `9600 8N1` with RTS/CTS disabled so first setup
works through the USB serial bridge.

Use `esp01_1m` for common 1 MB ESP-01 modules or `esp01` for older 512 KB
modules. The ESP-01 targets disable PerryFi RTS/CTS support at compile time
because normal ESP-01 modules do not expose the PerryFi flow-control pins.

## Build And Flash

With a global PlatformIO install:

```sh
pio run
pio run -t upload
```

For an ESP-01:

```sh
pio run -e esp01_1m
UPLOAD_PORT=/dev/ttyUSB0 pio run -e esp01_1m -t upload
```

Use `-e esp01` instead for a 512 KB ESP-01.

For an ESP-12F:

```sh
pio run -e esp12f
UPLOAD_PORT=/dev/ttyUSB0 pio run -e esp12f -t upload
```

To select a serial port explicitly:

```sh
UPLOAD_PORT=/dev/ttyUSB0 pio run -t upload
```

If PlatformIO is not installed globally, use a local virtual environment:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install platformio pyserial
env PLATFORMIO_CORE_DIR=.platformio .venv/bin/platformio run
env PLATFORMIO_CORE_DIR=.platformio UPLOAD_PORT=/dev/ttyUSB0 \
  .venv/bin/platformio run -t upload
```

For an ESP-01 with the local environment:

```sh
env PLATFORMIO_CORE_DIR=.platformio .venv/bin/platformio run -e esp01_1m
env PLATFORMIO_CORE_DIR=.platformio UPLOAD_PORT=/dev/ttyUSB0 \
  .venv/bin/platformio run -e esp01_1m -t upload
```

Use `-e esp01` instead for a 512 KB ESP-01.

For an ESP-12F with the local environment:

```sh
env PLATFORMIO_CORE_DIR=.platformio .venv/bin/platformio run -e esp12f
env PLATFORMIO_CORE_DIR=.platformio UPLOAD_PORT=/dev/ttyUSB0 \
  .venv/bin/platformio run -e esp12f -t upload
```

The generated firmware binary is:

```text
.pio/build/d1_mini/firmware.bin
```

For ESP-01 builds it is:

```text
.pio/build/esp01_1m/firmware.bin
```

or, for 512 KB modules:

```text
.pio/build/esp01/firmware.bin
```

For ESP-12F builds it is:

```text
.pio/build/esp12f/firmware.bin
```

## ESP-01 Flash Wiring

Use a 3.3 V USB-to-serial adapter. Do not use 5 V TTL levels. The ESP-01 also
needs a stable 3.3 V supply capable of short WiFi current bursts; weak USB TTL
adapter regulators are a common cause of failed boots and failed uploads.

Wire the adapter like this:

| USB serial adapter | ESP-01 |
| --- | --- |
| TXD | RX |
| RXD | TX |
| GND | GND |
| 3V3 | VCC |
| 3V3 | CH_PD / EN |
| 3V3 | GPIO2 |
| GND during reset/upload | GPIO0 |

To enter the bootloader:

1. connect GPIO0 to GND
2. reset or power-cycle the ESP-01
3. upload the firmware
4. disconnect GPIO0 from GND
5. reset or power-cycle again to run PerryNet

Some ESP-01 USB programmer boards have a flash/program switch that handles the
GPIO0 step for you.

## Configure WiFi

After flashing, configure WiFi over the PerryNet serial protocol:

```sh
python3 -m pip install --user pyserial
SSID='your wifi' PASS='your password' PORT=/dev/ttyUSB0 \
  python3 tools/wifi_config.py
```

If you are using the local virtual environment from above:

```sh
SSID='your wifi' PASS='your password' PORT=/dev/ttyUSB0 \
  .venv/bin/python tools/wifi_config.py
```

The tool sends `WIFI_SET`, saves settings with `SETTINGS_SAVE`, starts a
connection with `WIFI_CONNECT`, and prints the assigned IP address when the
connection succeeds. The firmware auto-connects on later boots once credentials
are saved.

To inspect the stored SSID without revealing the stored password:

```sh
PORT=/dev/ttyUSB0 .venv/bin/python tools/wifi_get.py
```

## Verify Internet Access

Run the HTTP connectivity test:

```sh
PORT=/dev/ttyUSB0 python3 tools/internet_test.py
```

Or with the local virtual environment:

```sh
PORT=/dev/ttyUSB0 .venv/bin/python tools/internet_test.py
```

The test checks WiFi status, resolves `example.com`, opens a TCP connection to
port 80, sends an HTTP request, pulls response data, and prints the result.

## Diagnose WiFi

If the device does not connect, read the WiFi diagnostic payload:

```sh
PORT=/dev/ttyUSB0 .venv/bin/python tools/wifi_diag.py
```

The diagnostic output includes station mode, PHY mode, sleep mode, current IP
configuration, RSSI, BSSID, last disconnect reason, and connection event
counters. This is useful for separating credential/AP problems from host serial
protocol problems.

## Check Firmware Time

PerryNet starts SNTP after WiFi comes up. Hosts can read the firmware-maintained
UTC clock with `TIME_GET`; GEOBENCH uses this for PCW desktop time sync.

`tools/internet_test.py` prints the firmware time as part of its diagnostics.
Immediately after boot the clock may still be invalid; retry after WiFi has
been connected for a short while.

## Recover From Saved Flow-Control Settings

Normal ESP8266 firmware uploads can preserve the EEPROM-emulation sector. If a
device was previously configured with RTS/CTS enabled, tools may time out on a
bare Wemos D1 mini because the USB serial bridge does not drive the PerryFi
flow-control pins.

Temporarily connect `D7` / GPIO13 to `GND`, reset the board, then save flow
control disabled:

```sh
PORT=/dev/ttyUSB0 python3 tools/uart_config.py --no-rtscts --save
```

Or with the local virtual environment:

```sh
PORT=/dev/ttyUSB0 .venv/bin/python tools/uart_config.py --no-rtscts --save
```

Remove the temporary `D7` to `GND` connection, reset again, then rerun
`tools/wifi_config.py`.
