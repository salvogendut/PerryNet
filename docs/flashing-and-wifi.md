# Flashing And First WiFi Setup

These steps assume a PerryFi-class Wemos D1 mini or compatible ESP8266 board
connected to the host over USB serial.

## Prerequisites

- PlatformIO, either installed globally as `pio` or in a local Python virtual
  environment.
- Python `pyserial` for the setup/test tools.
- A serial device such as `/dev/ttyUSB0` or `/dev/ttyACM0`.
- WiFi SSID and password for a 2.4 GHz network supported by the ESP8266.

The firmware target is `d1_mini`. Fresh builds default to `9600 8N1` with
RTS/CTS disabled so first setup works through the USB serial bridge.

## Build And Flash

With a global PlatformIO install:

```sh
pio run
pio run -t upload
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

The generated firmware binary is:

```text
.pio/build/d1_mini/firmware.bin
```

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
