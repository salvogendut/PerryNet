// SPDX-License-Identifier: GPL-3.0-or-later
//
// PerryNet: TCP/IP socket offload firmware for PerryFi-class ESP8266 hardware.

#include <Arduino.h>
#include <EEPROM.h>
#include <ESP8266WiFi.h>
#include <WiFiUdp.h>
#include <time.h>

extern "C" {
#include <uart_register.h>
}

#ifndef PERRYN_FW_NAME
#define PERRYN_FW_NAME "PerryNet"
#endif

#ifndef PERRYN_DEFAULT_RTSCTS
#define PERRYN_DEFAULT_RTSCTS 0
#endif

#ifndef PERRYN_HAS_RTSCTS
#define PERRYN_HAS_RTSCTS 1
#endif

#ifndef D7
#define D7 13
#endif

#ifndef D8
#define D8 15
#endif

namespace {

constexpr uint8_t PROTO_VERSION = 1;
constexpr uint8_t FW_MAJOR = 0;
constexpr uint8_t FW_MINOR = 1;

constexpr uint8_t SLIP_END = 0xC0;
constexpr uint8_t SLIP_ESC = 0xDB;
constexpr uint8_t SLIP_ESC_END = 0xDC;
constexpr uint8_t SLIP_ESC_ESC = 0xDD;

constexpr size_t MAX_PAYLOAD = 512;
constexpr size_t MAX_FRAME_BODY = 6 + MAX_PAYLOAD + 2;
constexpr size_t TCP_READ_CHUNK = 32;
constexpr size_t UDP_READ_CHUNK = 256;
constexpr uint32_t TCP_DATA_GAP_MS = 120;
constexpr uint32_t TIME_SYNC_RETRY_MS = 60000;
constexpr uint32_t TIME_VALID_AFTER = 1609459200UL; // 2021-01-01 UTC
constexpr uint8_t MAX_CHANNELS = 8;
constexpr uint8_t MAX_LISTENERS = 2;

constexpr uint32_t SETTINGS_MAGIC = 0x504E4554UL; // "PNET"
constexpr uint16_t SETTINGS_VERSION = 1;
constexpr uint32_t DEFAULT_BAUD = 9600;
constexpr uint8_t FLOW_RTS_PIN = D8;
constexpr uint8_t FLOW_CTS_PIN = D7;

enum Opcode : uint8_t {
  OP_HELLO = 0x01,
  OP_RESET_DEVICE = 0x02,
  OP_WIFI_GET = 0x10,
  OP_WIFI_SET = 0x11,
  OP_WIFI_CONNECT = 0x12,
  OP_WIFI_DISCONNECT = 0x13,
  OP_WIFI_STATUS = 0x14,
  OP_SETTINGS_SAVE = 0x15,
  OP_WIFI_DIAG = 0x16,
  OP_DNS_RESOLVE = 0x20,
  OP_TCP_OPEN = 0x30,
  OP_TCP_CLOSE = 0x31,
  OP_TCP_SEND = 0x32,
  OP_TCP_LISTEN = 0x33,
  OP_TCP_LISTEN_CLOSE = 0x34,
  OP_TCP_RECV = 0x35,
  OP_UDP_OPEN = 0x40,
  OP_UDP_CLOSE = 0x41,
  OP_UDP_SEND = 0x42,
  OP_UART_GET = 0x50,
  OP_UART_SET = 0x51,
  OP_TIME_GET = 0x60,
  OP_PING = 0x70,
  OP_ACK = 0x80,
  OP_EVENT = 0x81,
  OP_TCP_DATA = 0x82,
  OP_UDP_DATA = 0x83,
};

enum Status : uint8_t {
  ST_OK = 0x00,
  ST_BAD_FRAME = 0x01,
  ST_BAD_OPCODE = 0x02,
  ST_BAD_LENGTH = 0x03,
  ST_BAD_CHANNEL = 0x04,
  ST_NO_SLOT = 0x05,
  ST_WIFI_DOWN = 0x06,
  ST_CONNECT_FAILED = 0x07,
  ST_IO_ERROR = 0x08,
  ST_UNSUPPORTED = 0x09,
  ST_BUSY = 0x0A,
  ST_BAD_ARGUMENT = 0x0B,
};

enum Event : uint8_t {
  EVT_READY = 0x01,
  EVT_WIFI_UP = 0x02,
  EVT_WIFI_DOWN = 0x03,
  EVT_TCP_ACCEPT = 0x10,
  EVT_TCP_CLOSED = 0x11,
  EVT_TCP_ERROR = 0x12,
  EVT_UDP_ERROR = 0x20,
};

enum ChannelType : uint8_t {
  CH_UNUSED = 0,
  CH_TCP = 1,
  CH_UDP = 2,
};

struct Settings {
  uint32_t magic;
  uint16_t version;
  uint32_t baud;
  bool rtsCts;
  bool autoConnect;
  char ssid[33];
  char password[65];
  char hostname[33];
};

struct FrameView {
  uint8_t opcode = 0;
  uint8_t seq = 0;
  uint8_t channel = 0;
  const uint8_t *payload = nullptr;
  uint16_t length = 0;
};

struct Channel {
  ChannelType type = CH_UNUSED;
  WiFiClient tcp;
  WiFiUDP udp;
  bool tcpWasConnected = false;
  bool pullTcp = false;
  uint16_t localPort = 0;
  uint32_t nextTcpDataAt = 0;
};

struct Listener {
  bool used = false;
  uint8_t id = 0;
  uint16_t port = 0;
  WiFiServer *server = nullptr;
};

Settings settings;
Channel channels[MAX_CHANNELS];
Listener listeners[MAX_LISTENERS];

uint8_t rxBuf[MAX_FRAME_BODY];
size_t rxLen = 0;
bool rxEscaped = false;
bool pendingReadyEvent = true;
uint8_t lastWifiStatus = WL_IDLE_STATUS;

uint32_t pendingBaud = 0;
bool pendingRtsCts = true;
bool pendingUartApply = false;
uint32_t pendingUartApplyAt = 0;
bool timeConfigured = false;
uint32_t nextTimeConfigAt = 0;
WiFiEventHandler wifiConnectedHandler;
WiFiEventHandler wifiDisconnectedHandler;
WiFiEventHandler wifiGotIpHandler;
WiFiEventHandler wifiDhcpTimeoutHandler;
uint16_t lastWifiDisconnectReason = 0;
uint32_t wifiConnectAttempts = 0;
uint32_t wifiConnectedEvents = 0;
uint32_t wifiDisconnectedEvents = 0;
uint32_t wifiGotIpEvents = 0;
uint32_t wifiDhcpTimeoutEvents = 0;
uint32_t lastWifiConnectAttemptAt = 0;
uint32_t lastWifiConnectedAt = 0;
uint32_t lastWifiDisconnectedAt = 0;
uint32_t lastWifiGotIpAt = 0;
uint8_t lastWifiChannel = 0;
uint8_t lastWifiBssid[6] = {0};

uint16_t crc16Update(uint16_t crc, uint8_t data) {
  crc ^= static_cast<uint16_t>(data) << 8;
  for (uint8_t i = 0; i < 8; ++i) {
    if (crc & 0x8000) {
      crc = (crc << 1) ^ 0x1021;
    } else {
      crc <<= 1;
    }
  }
  return crc;
}

uint16_t crc16(const uint8_t *data, size_t len) {
  uint16_t crc = 0xFFFF;
  for (size_t i = 0; i < len; ++i) {
    crc = crc16Update(crc, data[i]);
  }
  return crc;
}

uint16_t readU16(const uint8_t *p) {
  return static_cast<uint16_t>(p[0]) | (static_cast<uint16_t>(p[1]) << 8);
}

uint32_t readU32(const uint8_t *p) {
  return static_cast<uint32_t>(p[0]) |
         (static_cast<uint32_t>(p[1]) << 8) |
         (static_cast<uint32_t>(p[2]) << 16) |
         (static_cast<uint32_t>(p[3]) << 24);
}

void appendU16(uint8_t *buf, size_t &len, uint16_t value) {
  buf[len++] = value & 0xFF;
  buf[len++] = value >> 8;
}

void appendU32(uint8_t *buf, size_t &len, uint32_t value) {
  buf[len++] = value & 0xFF;
  buf[len++] = (value >> 8) & 0xFF;
  buf[len++] = (value >> 16) & 0xFF;
  buf[len++] = (value >> 24) & 0xFF;
}

void appendIp(uint8_t *buf, size_t &len, const IPAddress &ip) {
  for (uint8_t i = 0; i < 4; ++i) {
    buf[len++] = ip[i];
  }
}

void appendMac(uint8_t *buf, size_t &len) {
  uint8_t mac[6] = {0};
  WiFi.macAddress(mac);
  for (uint8_t i = 0; i < 6; ++i) {
    buf[len++] = mac[i];
  }
}

void appendBytes(uint8_t *buf, size_t &len, const uint8_t *data, size_t dataLen) {
  for (size_t i = 0; i < dataLen; ++i) {
    buf[len++] = data[i];
  }
}

bool timeValid() {
  return static_cast<uint32_t>(time(nullptr)) >= TIME_VALID_AFTER;
}

void scheduleTimeSync() {
  timeConfigured = false;
  nextTimeConfigAt = 0;
}

void serviceTimeSync() {
  if (WiFi.status() != WL_CONNECTED || timeValid()) {
    return;
  }
  const uint32_t now = millis();
  if (timeConfigured &&
      static_cast<int32_t>(now - nextTimeConfigAt) < 0) {
    return;
  }
  configTime(0, 0, "time.google.com", "pool.ntp.org", "time.cloudflare.com");
  timeConfigured = true;
  nextTimeConfigAt = now + TIME_SYNC_RETRY_MS;
}

size_t boundedStrLen(const char *value, size_t maxLen) {
  size_t len = 0;
  while (len < maxLen && value[len]) {
    ++len;
  }
  return len;
}

uint32_t actualBaud(uint32_t requested) {
  // The PCW CPS8256 baud clock is PIT-derived, so some useful host-side
  // profiles need aliases to the closest exact PCW divisor.
  if (requested == 19200UL) {
    return 17857UL; // divisor 7, matching the original PerryFi timing quirk
  }
  if (requested == 38400UL) {
    return 41667UL; // divisor 3, used by GEOBENCH Telnet's fast pull mode
  }
  return requested;
}

void setHardwareFlow(bool enabled) {
#if PERRYN_HAS_RTSCTS
  if (enabled) {
    pinMode(FLOW_RTS_PIN, FUNCTION_4);
    SET_PERI_REG_BITS(UART_CONF1(0), UART_RX_FLOW_THRHD, 64, UART_RX_FLOW_THRHD_S);
    SET_PERI_REG_MASK(UART_CONF1(0), UART_RX_FLOW_EN);

    pinMode(FLOW_CTS_PIN, FUNCTION_4);
    SET_PERI_REG_MASK(UART_CONF0(0), UART_TX_FLOW_EN);
  } else {
    CLEAR_PERI_REG_MASK(UART_CONF1(0), UART_RX_FLOW_EN);
    CLEAR_PERI_REG_MASK(UART_CONF0(0), UART_TX_FLOW_EN);
    pinMode(FLOW_RTS_PIN, OUTPUT);
    digitalWrite(FLOW_RTS_PIN, LOW);
  }
#else
  (void)enabled;
  CLEAR_PERI_REG_MASK(UART_CONF1(0), UART_RX_FLOW_EN);
  CLEAR_PERI_REG_MASK(UART_CONF0(0), UART_TX_FLOW_EN);
#endif
}

void defaults() {
  memset(&settings, 0, sizeof(settings));
  settings.magic = SETTINGS_MAGIC;
  settings.version = SETTINGS_VERSION;
  settings.baud = DEFAULT_BAUD;
  settings.rtsCts = PERRYN_DEFAULT_RTSCTS != 0;
  settings.autoConnect = true;
  strncpy(settings.hostname, "perrynet", sizeof(settings.hostname) - 1);
  settings.hostname[sizeof(settings.hostname) - 1] = 0;
}

void loadSettings() {
  EEPROM.begin(sizeof(Settings));
  EEPROM.get(0, settings);
  if (settings.magic != SETTINGS_MAGIC || settings.version != SETTINGS_VERSION ||
      settings.baud < 300 || settings.baud > 115200) {
    defaults();
    EEPROM.put(0, settings);
    EEPROM.commit();
  } else if (!settings.autoConnect) {
    settings.autoConnect = true;
    EEPROM.put(0, settings);
    EEPROM.commit();
  }
}

bool saveSettings() {
  settings.magic = SETTINGS_MAGIC;
  settings.version = SETTINGS_VERSION;
  EEPROM.put(0, settings);
  return EEPROM.commit();
}

void slipWrite(uint8_t byte) {
  if (byte == SLIP_END) {
    Serial.write(SLIP_ESC);
    Serial.write(SLIP_ESC_END);
  } else if (byte == SLIP_ESC) {
    Serial.write(SLIP_ESC);
    Serial.write(SLIP_ESC_ESC);
  } else {
    Serial.write(byte);
  }
}

void slipWriteBytes(const uint8_t *data, size_t len) {
  for (size_t i = 0; i < len; ++i) {
    slipWrite(data[i]);
    if ((i & 0x3F) == 0x3F) {
      yield();
    }
  }
}

void sendFrame(uint8_t opcode, uint8_t seq, uint8_t channel,
               const uint8_t *payload, uint16_t length) {
  uint8_t header[6] = {
      PROTO_VERSION,
      opcode,
      seq,
      channel,
      static_cast<uint8_t>(length & 0xFF),
      static_cast<uint8_t>(length >> 8),
  };

  uint16_t crc = crc16(header, sizeof(header));
  for (uint16_t i = 0; i < length; ++i) {
    crc = crc16Update(crc, payload[i]);
  }

  Serial.write(SLIP_END);
  slipWriteBytes(header, sizeof(header));
  slipWriteBytes(payload, length);
  slipWrite(crc & 0xFF);
  slipWrite(crc >> 8);
  Serial.write(SLIP_END);
}

void sendAck(uint8_t seq, uint8_t channel, Status status,
             const uint8_t *payload = nullptr, uint16_t length = 0) {
  uint8_t response[MAX_PAYLOAD];
  if (static_cast<size_t>(length) + 1 > sizeof(response)) {
    status = ST_BAD_LENGTH;
    payload = nullptr;
    length = 0;
  }
  response[0] = status;
  if (payload && length) {
    memcpy(response + 1, payload, length);
  }
  sendFrame(OP_ACK, seq, channel, response, length + 1);
}

void sendEvent(uint8_t channel, Event event, const uint8_t *payload = nullptr,
               uint16_t length = 0) {
  uint8_t response[MAX_PAYLOAD];
  if (static_cast<size_t>(length) + 1 > sizeof(response)) {
    return;
  }
  response[0] = event;
  if (payload && length) {
    memcpy(response + 1, payload, length);
  }
  sendFrame(OP_EVENT, 0, channel, response, length + 1);
}

void appendNetworkStatus(uint8_t *buf, size_t &len, bool includeConnectedFlag) {
  const bool connected = WiFi.status() == WL_CONNECTED;
  buf[len++] = static_cast<uint8_t>(WiFi.status());
  if (includeConnectedFlag) {
    buf[len++] = connected ? 1 : 0;
  }
  appendU32(buf, len, static_cast<uint32_t>(WiFi.RSSI()));
  appendIp(buf, len, WiFi.localIP());
  appendIp(buf, len, WiFi.gatewayIP());
  appendIp(buf, len, WiFi.subnetMask());
  appendIp(buf, len, WiFi.dnsIP());
  appendMac(buf, len);
}

void sendWifiUpEvent() {
  uint8_t payload[1 + 4 + 4 + 4 + 4 + 4 + 6];
  size_t len = 0;
  appendNetworkStatus(payload, len, false);
  sendEvent(0, EVT_WIFI_UP, payload, len);
  scheduleTimeSync();
}

void resetChannelState(uint8_t index) {
  channels[index].type = CH_UNUSED;
  channels[index].tcpWasConnected = false;
  channels[index].pullTcp = false;
  channels[index].localPort = 0;
  channels[index].nextTcpDataAt = 0;
}

int findFreeChannel() {
  for (uint8_t i = 0; i < MAX_CHANNELS; ++i) {
    if (channels[i].type == CH_UNUSED) {
      return i;
    }
  }
  return -1;
}

Channel *getChannel(uint8_t id, ChannelType expected) {
  if (id == 0 || id > MAX_CHANNELS) {
    return nullptr;
  }
  Channel &channel = channels[id - 1];
  if (channel.type != expected) {
    return nullptr;
  }
  return &channel;
}

void closeChannel(uint8_t id, bool emitEvent) {
  Channel *channel = id == 0 || id > MAX_CHANNELS ? nullptr : &channels[id - 1];
  if (!channel || channel->type == CH_UNUSED) {
    return;
  }
  if (channel->type == CH_TCP) {
    channel->tcp.stop();
    if (emitEvent) {
      sendEvent(id, EVT_TCP_CLOSED);
    }
  } else if (channel->type == CH_UDP) {
    channel->udp.stop();
  }
  resetChannelState(id - 1);
}

Listener *getListener(uint8_t id) {
  if (id < 0x80 || id >= 0x80 + MAX_LISTENERS) {
    return nullptr;
  }
  Listener &listener = listeners[id - 0x80];
  return listener.used ? &listener : nullptr;
}

void closeListener(uint8_t id) {
  Listener *listener = getListener(id);
  if (!listener) {
    return;
  }
  if (listener->server) {
    listener->server->close();
    delete listener->server;
  }
  *listener = Listener();
}

void closeAllNetworkObjects() {
  for (uint8_t i = 1; i <= MAX_CHANNELS; ++i) {
    closeChannel(i, false);
  }
  for (uint8_t i = 0; i < MAX_LISTENERS; ++i) {
    closeListener(0x80 + i);
  }
}

bool readHostPort(const uint8_t *payload, uint16_t length,
                  char *host, size_t hostSize, uint16_t &port, uint8_t &flags) {
  if (length < 4) {
    return false;
  }
  const uint8_t hostLen = payload[0];
  if (hostLen == 0 || hostLen >= hostSize || length != 1 + hostLen + 2 + 1) {
    return false;
  }
  memcpy(host, payload + 1, hostLen);
  host[hostLen] = 0;
  port = readU16(payload + 1 + hostLen);
  flags = payload[1 + hostLen + 2];
  return port != 0;
}

void handleHello(const FrameView &frame) {
  uint8_t payload[64];
  size_t len = 0;
  payload[len++] = FW_MAJOR;
  payload[len++] = FW_MINOR;
  appendU16(payload, len, static_cast<uint16_t>(MAX_PAYLOAD));
  payload[len++] = MAX_CHANNELS;
  payload[len++] = MAX_LISTENERS;
  appendU32(payload, len, 0x7FUL);
  const char name[] = PERRYN_FW_NAME;
  memcpy(payload + len, name, sizeof(name));
  len += sizeof(name);
  sendAck(frame.seq, frame.channel, ST_OK, payload, len);
}

void handleWifiGet(const FrameView &frame) {
  uint8_t payload[2 + 32];
  size_t len = 0;
  const uint8_t ssidLen =
      static_cast<uint8_t>(boundedStrLen(settings.ssid, sizeof(settings.ssid)));
  payload[len++] = ssidLen;
  payload[len++] = settings.password[0] ? 1 : 0;
  memcpy(payload + len, settings.ssid, ssidLen);
  len += ssidLen;
  sendAck(frame.seq, frame.channel, ST_OK, payload, len);
}

void handleWifiSet(const FrameView &frame) {
  if (frame.length < 2) {
    sendAck(frame.seq, frame.channel, ST_BAD_LENGTH);
    return;
  }
  const uint8_t ssidLen = frame.payload[0];
  const uint8_t passLen = frame.payload[1];
  if (ssidLen > 32 || passLen > 64 || frame.length != 2 + ssidLen + passLen) {
    sendAck(frame.seq, frame.channel, ST_BAD_LENGTH);
    return;
  }
  memcpy(settings.ssid, frame.payload + 2, ssidLen);
  settings.ssid[ssidLen] = 0;
  memcpy(settings.password, frame.payload + 2 + ssidLen, passLen);
  settings.password[passLen] = 0;
  sendAck(frame.seq, frame.channel, ST_OK);
}

void connectWifi() {
  if (!settings.ssid[0]) {
    return;
  }
  ++wifiConnectAttempts;
  lastWifiConnectAttemptAt = millis();
  closeAllNetworkObjects();
  WiFi.mode(WIFI_STA);
  WiFi.enableAP(false);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  WiFi.hostname(settings.hostname);
  WiFi.disconnect(false, false);
  delay(100);
  WiFi.begin(settings.ssid, settings.password);
  WiFi.reconnect();
}

void handleWifiConnect(const FrameView &frame) {
  if (!settings.ssid[0]) {
    sendAck(frame.seq, frame.channel, ST_BAD_ARGUMENT);
    return;
  }
  connectWifi();
  sendAck(frame.seq, frame.channel, ST_OK);
}

void handleWifiDisconnect(const FrameView &frame) {
  WiFi.disconnect();
  closeAllNetworkObjects();
  sendAck(frame.seq, frame.channel, ST_OK);
}

void handleWifiStatus(const FrameView &frame) {
  uint8_t payload[1 + 1 + 4 + 4 + 4 + 4 + 4 + 6];
  size_t len = 0;
  appendNetworkStatus(payload, len, true);
  sendAck(frame.seq, frame.channel, ST_OK, payload, len);
}

void handleWifiDiag(const FrameView &frame) {
  uint8_t payload[96];
  size_t len = 0;
  payload[len++] = static_cast<uint8_t>(WiFi.status());
  payload[len++] = WiFi.isConnected() ? 1 : 0;
  payload[len++] = static_cast<uint8_t>(WiFi.getMode());
  payload[len++] = static_cast<uint8_t>(WiFi.getPhyMode());
  payload[len++] = static_cast<uint8_t>(WiFi.getSleepMode());
  payload[len++] = WiFi.channel();
  appendU32(payload, len, static_cast<uint32_t>(WiFi.RSSI()));
  appendIp(payload, len, WiFi.localIP());
  appendIp(payload, len, WiFi.gatewayIP());
  appendIp(payload, len, WiFi.subnetMask());
  appendIp(payload, len, WiFi.dnsIP());
  appendMac(payload, len);
  const uint8_t *bssid = WiFi.BSSID();
  appendBytes(payload, len, bssid ? bssid : lastWifiBssid, 6);
  appendU16(payload, len, lastWifiDisconnectReason);
  appendU32(payload, len, wifiConnectAttempts);
  appendU32(payload, len, wifiConnectedEvents);
  appendU32(payload, len, wifiDisconnectedEvents);
  appendU32(payload, len, wifiGotIpEvents);
  appendU32(payload, len, wifiDhcpTimeoutEvents);
  appendU32(payload, len, millis() - lastWifiConnectAttemptAt);
  appendU32(payload, len, lastWifiConnectedAt ? millis() - lastWifiConnectedAt : 0);
  appendU32(payload, len, lastWifiDisconnectedAt ? millis() - lastWifiDisconnectedAt : 0);
  appendU32(payload, len, lastWifiGotIpAt ? millis() - lastWifiGotIpAt : 0);
  payload[len++] = lastWifiChannel;
  sendAck(frame.seq, frame.channel, ST_OK, payload, len);
}

void handleDnsResolve(const FrameView &frame) {
  if (WiFi.status() != WL_CONNECTED) {
    sendAck(frame.seq, frame.channel, ST_WIFI_DOWN);
    return;
  }
  if (frame.length == 0 || frame.length > 253) {
    sendAck(frame.seq, frame.channel, ST_BAD_LENGTH);
    return;
  }
  char host[254];
  memcpy(host, frame.payload, frame.length);
  host[frame.length] = 0;

  IPAddress ip;
  if (!WiFi.hostByName(host, ip)) {
    sendAck(frame.seq, frame.channel, ST_CONNECT_FAILED);
    return;
  }
  uint8_t payload[4];
  size_t len = 0;
  appendIp(payload, len, ip);
  sendAck(frame.seq, frame.channel, ST_OK, payload, len);
}

void handleTcpOpen(const FrameView &frame) {
  if (WiFi.status() != WL_CONNECTED) {
    sendAck(frame.seq, frame.channel, ST_WIFI_DOWN);
    return;
  }

  char host[254];
  uint16_t port = 0;
  uint8_t flags = 0;
  if (!readHostPort(frame.payload, frame.length, host, sizeof(host), port, flags)) {
    sendAck(frame.seq, frame.channel, ST_BAD_LENGTH);
    return;
  }

  const int slot = findFreeChannel();
  if (slot < 0) {
    sendAck(frame.seq, frame.channel, ST_NO_SLOT);
    return;
  }

  Channel &channel = channels[slot];
  channel.type = CH_TCP;
  channel.tcp.setNoDelay(flags & 0x01);
  channel.pullTcp = (flags & 0x02) != 0;
  channel.nextTcpDataAt = millis() + TCP_DATA_GAP_MS;
  if (!channel.tcp.connect(host, port)) {
    channel.tcp.stop();
    resetChannelState(slot);
    sendAck(frame.seq, frame.channel, ST_CONNECT_FAILED);
    return;
  }
  channel.tcpWasConnected = true;

  uint8_t payload[1 + 4 + 2];
  size_t len = 0;
  payload[len++] = slot + 1;
  appendIp(payload, len, channel.tcp.localIP());
  appendU16(payload, len, channel.tcp.localPort());
  sendAck(frame.seq, frame.channel, ST_OK, payload, len);
}

void handleTcpClose(const FrameView &frame) {
  Channel *channel = getChannel(frame.channel, CH_TCP);
  if (!channel) {
    sendAck(frame.seq, frame.channel, ST_BAD_CHANNEL);
    return;
  }
  closeChannel(frame.channel, false);
  sendAck(frame.seq, frame.channel, ST_OK);
}

void handleTcpSend(const FrameView &frame) {
  Channel *channel = getChannel(frame.channel, CH_TCP);
  if (!channel) {
    sendAck(frame.seq, frame.channel, ST_BAD_CHANNEL);
    return;
  }
  if (!channel->tcp.connected()) {
    sendAck(frame.seq, frame.channel, ST_IO_ERROR);
    return;
  }
  const size_t written = channel->tcp.write(frame.payload, frame.length);
  uint8_t payload[2];
  size_t len = 0;
  appendU16(payload, len, static_cast<uint16_t>(written));
  sendAck(frame.seq, frame.channel,
          written == frame.length ? ST_OK : ST_IO_ERROR, payload, len);
}

void handleTcpRecv(const FrameView &frame) {
  Channel *channel = getChannel(frame.channel, CH_TCP);
  if (!channel) {
    sendAck(frame.seq, frame.channel, ST_BAD_CHANNEL);
    return;
  }
  if (frame.length != 0 && frame.length != 2) {
    sendAck(frame.seq, frame.channel, ST_BAD_LENGTH);
    return;
  }

  uint16_t maxLen = TCP_READ_CHUNK;
  if (frame.length == 2) {
    maxLen = readU16(frame.payload);
  }
  if (maxLen > MAX_PAYLOAD - 1) {
    maxLen = MAX_PAYLOAD - 1;
  }

  uint8_t payload[MAX_PAYLOAD - 1];
  int got = 0;
  if (maxLen && channel->tcp.available() > 0) {
    const int available = channel->tcp.available();
    const int limit = static_cast<int>(maxLen);
    got = channel->tcp.read(payload, available < limit ? available : limit);
    if (got < 0) {
      sendAck(frame.seq, frame.channel, ST_IO_ERROR);
      return;
    }
  }

  sendAck(frame.seq, frame.channel, ST_OK, payload, static_cast<uint16_t>(got));
  if (channel->tcpWasConnected && !channel->tcp.connected() &&
      channel->tcp.available() == 0) {
    closeChannel(frame.channel, true);
  }
}

void handleTcpListen(const FrameView &frame) {
  if (WiFi.status() != WL_CONNECTED) {
    sendAck(frame.seq, frame.channel, ST_WIFI_DOWN);
    return;
  }
  if (frame.length != 2) {
    sendAck(frame.seq, frame.channel, ST_BAD_LENGTH);
    return;
  }
  const uint16_t port = readU16(frame.payload);
  if (port == 0) {
    sendAck(frame.seq, frame.channel, ST_BAD_ARGUMENT);
    return;
  }
  int slot = -1;
  for (uint8_t i = 0; i < MAX_LISTENERS; ++i) {
    if (!listeners[i].used) {
      slot = i;
      break;
    }
  }
  if (slot < 0) {
    sendAck(frame.seq, frame.channel, ST_NO_SLOT);
    return;
  }

  Listener &listener = listeners[slot];
  listener.server = new WiFiServer(port);
  if (!listener.server) {
    listener = Listener();
    sendAck(frame.seq, frame.channel, ST_NO_SLOT);
    return;
  }
  listener.used = true;
  listener.id = static_cast<uint8_t>(0x80 + slot);
  listener.port = port;
  listener.server->begin();
  listener.server->setNoDelay(true);

  uint8_t payload[3];
  size_t len = 0;
  payload[len++] = listener.id;
  appendU16(payload, len, port);
  sendAck(frame.seq, frame.channel, ST_OK, payload, len);
}

void handleTcpListenClose(const FrameView &frame) {
  if (!getListener(frame.channel)) {
    sendAck(frame.seq, frame.channel, ST_BAD_CHANNEL);
    return;
  }
  closeListener(frame.channel);
  sendAck(frame.seq, frame.channel, ST_OK);
}

void handleUdpOpen(const FrameView &frame) {
  if (WiFi.status() != WL_CONNECTED) {
    sendAck(frame.seq, frame.channel, ST_WIFI_DOWN);
    return;
  }
  if (frame.length != 2) {
    sendAck(frame.seq, frame.channel, ST_BAD_LENGTH);
    return;
  }
  const uint16_t localPort = readU16(frame.payload);
  const int slot = findFreeChannel();
  if (slot < 0) {
    sendAck(frame.seq, frame.channel, ST_NO_SLOT);
    return;
  }

  Channel &channel = channels[slot];
  channel.type = CH_UDP;
  if (!channel.udp.begin(localPort)) {
    resetChannelState(slot);
    sendAck(frame.seq, frame.channel, ST_IO_ERROR);
    return;
  }
  channel.localPort = localPort;

  uint8_t payload[3];
  size_t len = 0;
  payload[len++] = slot + 1;
  appendU16(payload, len, localPort);
  sendAck(frame.seq, frame.channel, ST_OK, payload, len);
}

void handleUdpClose(const FrameView &frame) {
  Channel *channel = getChannel(frame.channel, CH_UDP);
  if (!channel) {
    sendAck(frame.seq, frame.channel, ST_BAD_CHANNEL);
    return;
  }
  channel->udp.stop();
  resetChannelState(frame.channel - 1);
  sendAck(frame.seq, frame.channel, ST_OK);
}

void handleUdpSend(const FrameView &frame) {
  Channel *channel = getChannel(frame.channel, CH_UDP);
  if (!channel) {
    sendAck(frame.seq, frame.channel, ST_BAD_CHANNEL);
    return;
  }
  if (frame.length < 6) {
    sendAck(frame.seq, frame.channel, ST_BAD_LENGTH);
    return;
  }

  IPAddress ip(frame.payload[0], frame.payload[1], frame.payload[2], frame.payload[3]);
  const uint16_t port = readU16(frame.payload + 4);
  if (port == 0) {
    sendAck(frame.seq, frame.channel, ST_BAD_ARGUMENT);
    return;
  }
  const uint8_t *data = frame.payload + 6;
  const uint16_t dataLen = frame.length - 6;
  if (!channel->udp.beginPacket(ip, port)) {
    sendAck(frame.seq, frame.channel, ST_IO_ERROR);
    return;
  }
  const size_t written = channel->udp.write(data, dataLen);
  const bool ended = channel->udp.endPacket() == 1;

  uint8_t payload[2];
  size_t len = 0;
  appendU16(payload, len, static_cast<uint16_t>(written));
  sendAck(frame.seq, frame.channel,
          ended && written == dataLen ? ST_OK : ST_IO_ERROR, payload, len);
}

void handleUartGet(const FrameView &frame) {
  uint8_t payload[5];
  size_t len = 0;
  appendU32(payload, len, settings.baud);
  payload[len++] = settings.rtsCts ? 0x01 : 0x00;
  sendAck(frame.seq, frame.channel, ST_OK, payload, len);
}

void handleUartSet(const FrameView &frame) {
  if (frame.length != 5) {
    sendAck(frame.seq, frame.channel, ST_BAD_LENGTH);
    return;
  }
  const uint32_t baud = readU32(frame.payload);
  const uint8_t flags = frame.payload[4];
  switch (baud) {
    case 300:
    case 600:
    case 1200:
    case 2400:
    case 4800:
    case 9600:
    case 19200:
    case 38400:
    case 57600:
    case 115200:
      break;
    default:
      sendAck(frame.seq, frame.channel, ST_BAD_ARGUMENT);
      return;
  }

  settings.baud = baud;
#if !PERRYN_HAS_RTSCTS
  if (flags & 0x01) {
    sendAck(frame.seq, frame.channel, ST_UNSUPPORTED);
    return;
  }
#endif
  settings.rtsCts = flags & 0x01;
  if (flags & 0x02) {
    saveSettings();
  }

  sendAck(frame.seq, frame.channel, ST_OK);
  Serial.flush();
  pendingBaud = baud;
  pendingRtsCts = settings.rtsCts;
  pendingUartApply = true;
  pendingUartApplyAt = millis() + 100;
}

void handleTimeGet(const FrameView &frame) {
  uint8_t payload[1 + 4 + 4];
  size_t len = 0;
  const uint32_t now = timeValid() ? static_cast<uint32_t>(time(nullptr)) : 0;
  payload[len++] = now ? 1 : 0;
  appendU32(payload, len, now);
  appendU32(payload, len, millis());
  sendAck(frame.seq, frame.channel, ST_OK, payload, len);
}

void processFrame(const uint8_t *body, size_t len) {
  if (len < 8) {
    sendAck(0, 0, ST_BAD_FRAME);
    return;
  }
  const uint16_t payloadLen = readU16(body + 4);
  const size_t expectedLen = 6 + static_cast<size_t>(payloadLen) + 2;
  if (payloadLen > MAX_PAYLOAD || len != expectedLen) {
    sendAck(body[2], body[3], ST_BAD_LENGTH);
    return;
  }
  const uint16_t expectedCrc = readU16(body + 6 + payloadLen);
  const uint16_t actualCrc = crc16(body, 6 + payloadLen);
  if (expectedCrc != actualCrc || body[0] != PROTO_VERSION) {
    sendAck(body[2], body[3], ST_BAD_FRAME);
    return;
  }

  FrameView frame;
  frame.opcode = body[1];
  frame.seq = body[2];
  frame.channel = body[3];
  frame.length = payloadLen;
  frame.payload = body + 6;

  switch (frame.opcode) {
    case OP_HELLO:
      handleHello(frame);
      break;
    case OP_RESET_DEVICE:
      sendAck(frame.seq, frame.channel, ST_OK);
      Serial.flush();
      ESP.restart();
      break;
    case OP_WIFI_GET:
      handleWifiGet(frame);
      break;
    case OP_WIFI_SET:
      handleWifiSet(frame);
      break;
    case OP_WIFI_CONNECT:
      handleWifiConnect(frame);
      break;
    case OP_WIFI_DISCONNECT:
      handleWifiDisconnect(frame);
      break;
    case OP_WIFI_STATUS:
      handleWifiStatus(frame);
      break;
    case OP_SETTINGS_SAVE:
      sendAck(frame.seq, frame.channel, saveSettings() ? ST_OK : ST_IO_ERROR);
      break;
    case OP_WIFI_DIAG:
      handleWifiDiag(frame);
      break;
    case OP_DNS_RESOLVE:
      handleDnsResolve(frame);
      break;
    case OP_TCP_OPEN:
      handleTcpOpen(frame);
      break;
    case OP_TCP_CLOSE:
      handleTcpClose(frame);
      break;
    case OP_TCP_SEND:
      handleTcpSend(frame);
      break;
    case OP_TCP_RECV:
      handleTcpRecv(frame);
      break;
    case OP_TCP_LISTEN:
      handleTcpListen(frame);
      break;
    case OP_TCP_LISTEN_CLOSE:
      handleTcpListenClose(frame);
      break;
    case OP_UDP_OPEN:
      handleUdpOpen(frame);
      break;
    case OP_UDP_CLOSE:
      handleUdpClose(frame);
      break;
    case OP_UDP_SEND:
      handleUdpSend(frame);
      break;
    case OP_UART_GET:
      handleUartGet(frame);
      break;
    case OP_UART_SET:
      handleUartSet(frame);
      break;
    case OP_TIME_GET:
      handleTimeGet(frame);
      break;
    case OP_PING:
      sendAck(frame.seq, frame.channel, ST_OK, frame.payload, frame.length);
      break;
    default:
      sendAck(frame.seq, frame.channel, ST_BAD_OPCODE);
      break;
  }
}

void pollSerial() {
  while (Serial.available()) {
    const uint8_t byte = Serial.read();
    if (byte == SLIP_END) {
      if (rxLen > 0) {
        processFrame(rxBuf, rxLen);
        rxLen = 0;
      }
      rxEscaped = false;
      continue;
    }

    uint8_t decoded = byte;
    if (rxEscaped) {
      if (byte == SLIP_ESC_END) {
        decoded = SLIP_END;
      } else if (byte == SLIP_ESC_ESC) {
        decoded = SLIP_ESC;
      } else {
        rxLen = 0;
        rxEscaped = false;
        sendAck(0, 0, ST_BAD_FRAME);
        continue;
      }
      rxEscaped = false;
    } else if (byte == SLIP_ESC) {
      rxEscaped = true;
      continue;
    }

    if (rxLen >= sizeof(rxBuf)) {
      rxLen = 0;
      rxEscaped = false;
      sendAck(0, 0, ST_BAD_LENGTH);
      continue;
    }
    rxBuf[rxLen++] = decoded;
  }
}

void serviceListeners() {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }
  for (uint8_t i = 0; i < MAX_LISTENERS; ++i) {
    Listener &listener = listeners[i];
    if (!listener.used || !listener.server || !listener.server->hasClient()) {
      continue;
    }

    const int slot = findFreeChannel();
    WiFiClient accepted = listener.server->accept();
    if (slot < 0) {
      accepted.println(F("BUSY"));
      accepted.stop();
      continue;
    }

    Channel &channel = channels[slot];
    channel.type = CH_TCP;
    channel.tcp = accepted;
    channel.tcp.setNoDelay(true);
    channel.tcpWasConnected = true;
    channel.nextTcpDataAt = millis() + TCP_DATA_GAP_MS;

    uint8_t payload[1 + 4 + 2];
    size_t len = 0;
    payload[len++] = listener.id;
    appendIp(payload, len, channel.tcp.remoteIP());
    appendU16(payload, len, channel.tcp.remotePort());
    sendEvent(slot + 1, EVT_TCP_ACCEPT, payload, len);
  }
}

void serviceTcpChannels() {
  uint8_t payload[TCP_READ_CHUNK];
  const uint32_t now = millis();
  for (uint8_t i = 0; i < MAX_CHANNELS; ++i) {
    Channel &channel = channels[i];
    if (channel.type != CH_TCP) {
      continue;
    }

    if (!channel.pullTcp &&
        channel.tcp.available() > 0 &&
        static_cast<int32_t>(now - channel.nextTcpDataAt) >= 0) {
      const int available = channel.tcp.available();
      const int limit = static_cast<int>(sizeof(payload));
      const int toRead = available < limit ? available : limit;
      const int got = channel.tcp.read(payload, toRead);
      if (got > 0) {
        sendFrame(OP_TCP_DATA, 0, i + 1, payload, got);
        channel.nextTcpDataAt = millis() + TCP_DATA_GAP_MS;
        yield();
      }
    }

    if (channel.tcpWasConnected && !channel.tcp.connected() && channel.tcp.available() == 0) {
      closeChannel(i + 1, true);
    }
  }
}

void serviceUdpChannels() {
  uint8_t payload[6 + UDP_READ_CHUNK];
  for (uint8_t i = 0; i < MAX_CHANNELS; ++i) {
    Channel &channel = channels[i];
    if (channel.type != CH_UDP) {
      continue;
    }

    int packetLen = channel.udp.parsePacket();
    while (packetLen > 0) {
      size_t len = 0;
      appendIp(payload, len, channel.udp.remoteIP());
      appendU16(payload, len, channel.udp.remotePort());
      const int limit = static_cast<int>(UDP_READ_CHUNK);
      const int toRead = packetLen < limit ? packetLen : limit;
      const int got = channel.udp.read(payload + len, toRead);
      if (got > 0) {
        len += got;
        sendFrame(OP_UDP_DATA, 0, i + 1, payload, len);
      }
      while (channel.udp.available()) {
        channel.udp.read();
      }
      packetLen = channel.udp.parsePacket();
      yield();
    }
  }
}

void serviceWifiEvents() {
  const uint8_t status = WiFi.status();
  if (status != lastWifiStatus) {
    if (status == WL_CONNECTED) {
      sendWifiUpEvent();
    } else if (lastWifiStatus == WL_CONNECTED) {
      closeAllNetworkObjects();
      sendEvent(0, EVT_WIFI_DOWN);
    }
    lastWifiStatus = status;
  }
}

void applyPendingUartSettings() {
  if (!pendingUartApply || millis() < pendingUartApplyAt) {
    return;
  }
  pendingUartApply = false;
  setHardwareFlow(false);
  Serial.updateBaudRate(actualBaud(pendingBaud));
  setHardwareFlow(pendingRtsCts);
}

} // namespace

void setup() {
  loadSettings();

  settings.baud = DEFAULT_BAUD;
  settings.rtsCts = false;

#if PERRYN_HAS_RTSCTS
  pinMode(FLOW_RTS_PIN, OUTPUT);
  digitalWrite(FLOW_RTS_PIN, LOW);
#endif
  Serial.setRxBufferSize(512);
  Serial.begin(actualBaud(settings.baud), SERIAL_8N1);
  setHardwareFlow(settings.rtsCts);

  WiFi.persistent(false);
  WiFi.mode(WIFI_STA);
  WiFi.enableAP(false);
  WiFi.setSleepMode(WIFI_NONE_SLEEP);
  WiFi.setAutoReconnect(true);
  WiFi.hostname(settings.hostname);
  WiFiClient::setDefaultNoDelay(true);

  wifiConnectedHandler = WiFi.onStationModeConnected([](const WiFiEventStationModeConnected &event) {
    ++wifiConnectedEvents;
    lastWifiConnectedAt = millis();
    lastWifiChannel = event.channel;
    memcpy(lastWifiBssid, event.bssid, sizeof(lastWifiBssid));
  });
  wifiDisconnectedHandler = WiFi.onStationModeDisconnected([](const WiFiEventStationModeDisconnected &event) {
    ++wifiDisconnectedEvents;
    lastWifiDisconnectedAt = millis();
    lastWifiDisconnectReason = static_cast<uint16_t>(event.reason);
    memcpy(lastWifiBssid, event.bssid, sizeof(lastWifiBssid));
  });
  wifiGotIpHandler = WiFi.onStationModeGotIP([](const WiFiEventStationModeGotIP &) {
    ++wifiGotIpEvents;
    lastWifiGotIpAt = millis();
  });
  wifiDhcpTimeoutHandler = WiFi.onStationModeDHCPTimeout([]() {
    ++wifiDhcpTimeoutEvents;
  });

  if (settings.ssid[0]) {
    connectWifi();
  }

  lastWifiStatus = WiFi.status();
}

void loop() {
  pollSerial();

  if (pendingReadyEvent) {
    pendingReadyEvent = false;
    sendEvent(0, EVT_READY);
  }

  serviceWifiEvents();
  serviceTimeSync();
  serviceListeners();
  serviceTcpChannels();
  serviceUdpChannels();
  applyPendingUartSettings();
  yield();
}
