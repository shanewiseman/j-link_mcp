#include <Arduino.h>
#include <ArduinoBLE.h>
#include <Arduino_CAN.h>
#include <Arduino_USBHostMbed5.h>
#include <SPI.h>
#include <USBHost/USBHost.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

#include "BridgeResources.h"
#include "BridgeWire.h"

#if __has_include("BridgeBuildIdentity.generated.h")
#include "BridgeBuildIdentity.generated.h"
#else
#define BRIDGE_FIRMWARE_VERSION "development"
#define BRIDGE_WIRE_VERSION 1
#define BRIDGE_BUILD_ID "development"
#define BRIDGE_BUILD_TIMESTAMP "unreleased"
#define BRIDGE_SOURCE_SHA256 \
  "0000000000000000000000000000000000000000000000000000000000000000"
#endif

using namespace bridge;

namespace {

constexpr uint16_t kStatusOk = 0;
constexpr uint16_t kMalformed = 1;
constexpr uint16_t kUnsupported = 2;
constexpr uint16_t kConflict = 3;
constexpr uint16_t kIoError = 4;
constexpr uint16_t kSelectionError = 5;
constexpr uint16_t kResourceOwnerGpio = 1;
constexpr uint16_t kResourceOwnerSpi0 = 2;
constexpr uint16_t kResourceOwnerSpi1 = 3;
constexpr size_t kMaxApplicationTransfer = 64000;
constexpr size_t kMaxMetadata = 4096;
constexpr size_t kEncodedCapacity = kMaxDecodedFrame + kMaxDecodedFrame / 254 + 4;

uint8_t encoded_input[kEncodedCapacity];
uint8_t decoded_frame[kMaxDecodedFrame];
uint8_t message_assembly[kMaxMessage];
uint8_t response_body[kMaxMessage];
uint8_t response_data[kMaxApplicationTransfer];
uint8_t frame_output[kMaxDecodedFrame];
uint8_t encoded_output[kEncodedCapacity];
char metadata_buffer[kMaxMetadata];

size_t encoded_length = 0;
size_t assembly_length = 0;
uint32_t assembly_request_id = 0;
uint16_t assembly_segment_count = 0;
uint16_t assembly_next_segment = 0;

PinResources pin_resources;
SharedReceiveQueue receive_queue;

bool uart_configured[4] = {false, false, false, false};
bool can_configured[2] = {false, false};
uint8_t gpio_edge[103] = {};
uint8_t gpio_last[103] = {};
bool gpio_configured[103] = {};
bool gpio_output[103] = {};

USBHost* usb_host = nullptr;
USBDeviceConnected* usb_device = nullptr;
uint8_t usb_interface = 0;

uint8_t radio_mode = 0;  // 0 none, 1 Wi-Fi, 2 BLE.
bool wifi_connected = false;
WiFiClient tcp_clients[8];
WiFiUDP udp_sockets[8];
bool socket_used[8] = {};
uint8_t socket_protocol[8] = {};
char socket_host[8][254] = {};
uint16_t socket_port[8] = {};

BLEDevice ble_peer;
BLECharacteristic ble_subscription;
bool ble_initialized = false;
bool ble_subscribed = false;
uint32_t expected_pairing_code = UINT32_MAX;
uint32_t displayed_pairing_code = UINT32_MAX;

uint64_t timestampUs() { return static_cast<uint64_t>(micros()); }

bool knownField(uint16_t operation, uint16_t field) {
  if (field == OPERATION) return true;
  switch (operation) {
    case GET_STATUS:
      return false;
    case RECEIVE:
      return field == PROTOCOL || field == CHANNEL || field == LIMIT ||
             field == DRAIN || field == TIMEOUT_MS;
    case UART_CONFIGURE:
      return field == UART_PORT || field == BAUDRATE || field == DATA_BITS ||
             field == PARITY || field == STOP_BITS;
    case CAN_CONFIGURE:
      return field == BUS || field == CAN_BITRATE;
    case USB_ENUMERATE:
    case USB_RESET:
    case USB_RELEASE:
    case WIFI_DISCONNECT:
    case BLE_DISCONNECT:
      return false;
    case USB_SELECT:
      return field == VID || field == PID || field == SERIAL_NUMBER ||
             field == USB_INTERFACE;
    case WIFI_CONNECT:
      return field == PROFILE || field == SSID || field == PASSWORD;
    case WIFI_SOCKET_OPEN:
      return field == SOCKET_PROTOCOL || field == HOST ||
             field == NETWORK_PORT || field == LOCAL_PORT;
    case WIFI_SOCKET_CLOSE:
      return field == SOCKET_ID;
    case BLE_SCAN:
      return field == SCAN_DURATION_MS || field == SERVICE_UUID;
    case BLE_CONNECT:
      return field == BLE_ADDRESS;
    case BLE_PAIR:
      return field == PASSKEY;
    case BLE_DISCOVER:
      return field == SERVICE_UUID;
    case BLE_SUBSCRIBE:
      return field == SERVICE_UUID || field == CHARACTERISTIC_UUID ||
             field == ENABLED;
    case GPIO_CONFIGURE:
      return field == PIN || field == GPIO_MODE || field == PULL ||
             field == INITIAL_VALUE;
    case GPIO_WATCH:
      return field == PIN || field == EDGE;
    case SPI_EXCHANGE:
      return field == BUS || field == CHIP_SELECT || field == CLOCK_HZ ||
             field == MODE || field == BIT_ORDER || field == FILL_BYTE ||
             field == DATA || field == READ_LENGTH;
    case I2C_EXCHANGE:
      return field == BUS || field == I2C_ADDRESS || field == CLOCK_HZ ||
             field == DATA || field == READ_LENGTH ||
             field == REPEATED_START;
    case UART_WRITE:
      return field == UART_PORT || field == DATA;
    case CAN_SEND:
      return field == BUS || field == CAN_ID || field == CAN_EXTENDED ||
             field == DATA;
    case USB_TRANSFER:
      return field == TRANSFER_TYPE || field == ENDPOINT ||
             field == READ_LENGTH || field == REQUEST_TYPE ||
             field == REQUEST_CODE || field == VALUE || field == INDEX ||
             field == DATA;
    case WIFI_SEND:
      return field == SOCKET_ID || field == DATA;
    case BLE_READ:
      return field == SERVICE_UUID || field == CHARACTERISTIC_UUID;
    case BLE_WRITE:
      return field == SERVICE_UUID || field == CHARACTERISTIC_UUID ||
             field == WRITE_MODE || field == DATA;
    case GPIO_READ:
      return field == PIN;
    case GPIO_WRITE:
      return field == PIN || field == GPIO_VALUE;
    case GPIO_PULSE:
      return field == PIN || field == GPIO_VALUE || field == DURATION_US;
    default:
      return false;
  }
}

void clearAssembly() {
  if (assembly_length) memset(message_assembly, 0, assembly_length);
  assembly_length = 0;
  assembly_request_id = 0;
  assembly_segment_count = 0;
  assembly_next_segment = 0;
}

void sendMessage(uint32_t request_id, uint8_t message_type,
                 const uint8_t* body, size_t body_length) {
  constexpr size_t kSegmentPayload = kMaxDecodedFrame - sizeof(FrameHeader);
  const uint16_t segment_count =
      max<uint16_t>(1, static_cast<uint16_t>((body_length + kSegmentPayload - 1) /
                                             kSegmentPayload));
  for (uint16_t segment = 0; segment < segment_count; ++segment) {
    const size_t offset = static_cast<size_t>(segment) * kSegmentPayload;
    const uint16_t payload_length = static_cast<uint16_t>(
        min(kSegmentPayload, body_length > offset ? body_length - offset : 0));
    FrameHeader header{};
    memcpy(header.magic, kMagic, sizeof(kMagic));
    header.wire_version = kWireVersion;
    header.message_type = message_type;
    header.request_id = request_id;
    header.segment_index = segment;
    header.segment_count = segment_count;
    header.payload_length = payload_length;
    memcpy(frame_output, &header, sizeof(header));
    if (payload_length) {
      memcpy(frame_output + sizeof(header), body + offset, payload_length);
    }
    header.crc32 = frameCrc(header, frame_output + sizeof(header));
    memcpy(frame_output, &header, sizeof(header));
    const size_t encoded = cobsEncode(
        frame_output, sizeof(header) + payload_length, encoded_output,
        sizeof(encoded_output));
    if (!encoded) return;
    Serial.write(encoded_output, encoded);
    Serial.write(static_cast<uint8_t>(0));
  }
}

void sendResponse(uint32_t request_id, uint16_t status, const uint8_t* data,
                  uint16_t data_length, const char* metadata,
                  uint16_t queue_depth = 0, uint32_t overflow = 0,
                  const char* error = nullptr) {
  TlvWriter writer(response_body, sizeof(response_body));
  bool ok = writer.u16(STATUS, status);
  if (data_length) ok = ok && writer.append(RESPONSE_DATA, data, data_length);
  if (metadata && metadata[0]) {
    const size_t length = strnlen(metadata, kMaxMetadata);
    ok = ok && length < kMaxMetadata &&
         writer.append(METADATA_JSON, metadata, static_cast<uint16_t>(length));
  }
  if (queue_depth) ok = ok && writer.u16(QUEUE_DEPTH, queue_depth);
  if (overflow) ok = ok && writer.u32(OVERFLOW_COUNT, overflow);
  ok = ok && writer.u64(TIMESTAMP_US, timestampUs());
  if (error && error[0]) {
    const size_t length = min<size_t>(strlen(error), 160);
    ok = ok && writer.append(ERROR_MESSAGE, error, static_cast<uint16_t>(length));
  }
  if (!ok) {
    TlvWriter fallback(response_body, sizeof(response_body));
    fallback.u16(STATUS, kIoError);
    constexpr char message[] = "response exceeds bridge bounds";
    fallback.append(ERROR_MESSAGE, message, sizeof(message) - 1);
    sendMessage(request_id, ERROR_RESPONSE, response_body, fallback.length());
    return;
  }
  sendMessage(request_id, status == 0 ? RESPONSE : ERROR_RESPONSE, response_body,
              writer.length());
}

void sendError(uint32_t request_id, uint16_t status, const char* error) {
  sendResponse(request_id, status, nullptr, 0, "{}", 0, 0, error);
}

bool copyString(const TlvReader& reader, uint16_t field, char* output,
                size_t capacity, bool required = true) {
  const uint8_t* value = nullptr;
  uint16_t length = 0;
  if (!reader.bytes(field, value, length, required)) return false;
  if (!value && !required) {
    output[0] = '\0';
    return true;
  }
  if (length == 0 || length >= capacity) return false;
  memcpy(output, value, length);
  output[length] = '\0';
  return true;
}

void appendText(char* destination, size_t capacity, const char* text) {
  const size_t current = strnlen(destination, capacity);
  if (current >= capacity) return;
  strncat(destination, text, capacity - current - 1);
}

bool encodeJsonString(const char* input, char* output, size_t capacity) {
  if (!input || capacity < 3) return false;
  size_t written = 0;
  output[written++] = '"';
  for (const uint8_t* cursor = reinterpret_cast<const uint8_t*>(input); *cursor;
       ++cursor) {
    char encoded[7] = {};
    size_t encoded_length = 1;
    if (*cursor == '"' || *cursor == '\\') {
      encoded[0] = '\\';
      encoded[1] = static_cast<char>(*cursor);
      encoded_length = 2;
    } else if (*cursor < 0x20 || *cursor > 0x7E) {
      snprintf(encoded, sizeof(encoded), "\\u00%02X", *cursor);
      encoded_length = 6;
    } else {
      encoded[0] = static_cast<char>(*cursor);
    }
    if (written + encoded_length + 2 > capacity) {
      output[0] = '\0';
      return false;
    }
    memcpy(output + written, encoded, encoded_length);
    written += encoded_length;
  }
  output[written++] = '"';
  output[written] = '\0';
  return true;
}

void buildStatusMetadata() {
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"firmware_version\":\"%s\",\"wire_version\":%u,"
           "\"build_id\":\"%s\",\"source_sha256\":\"%s\","
           "\"supported_interfaces\":[\"spi\",\"i2c\",\"uart\","
           "\"can\",\"usb\",\"wifi\",\"ble\",\"gpio\"],"
           "\"safe_pins\":[",
           BRIDGE_FIRMWARE_VERSION, BRIDGE_WIRE_VERSION, BRIDGE_BUILD_ID,
           BRIDGE_SOURCE_SHA256);
  bool first = true;
  char pin[16];
  for (uint16_t number = 2; number <= 85; ++number) {
    if (!safeDynamicPin(number)) continue;
    snprintf(pin, sizeof(pin), "%s\"D%u\"", first ? "" : ",", number);
    appendText(metadata_buffer, sizeof(metadata_buffer), pin);
    first = false;
  }
  char tail[1800];
  snprintf(
      tail, sizeof(tail),
      "],\"transfer_limits\":{\"decoded_frame\":4096,"
      "\"assembled_message\":65536,\"application_payload\":64000,"
      "\"i2c_transaction\":32,\"can_payload\":8,"
      "\"usb_interrupt\":4096},\"connections\":{"
      "\"wifi\":%s,\"ble\":%s,\"usb\":%s},"
      "\"queue_depths\":{\"uart\":%lu,\"can\":%lu,\"usb\":%lu,"
      "\"wifi\":%lu,\"ble\":%lu,\"gpio\":%lu},"
      "\"overflow_counts\":{\"uart\":%lu,\"can\":%lu,\"usb\":%lu,"
      "\"wifi\":%lu,\"ble\":%lu,\"gpio\":%lu},"
      "\"active_resource_conflicts\":[]}",
      wifi_connected ? "true" : "false",
      (ble_peer && ble_peer.connected()) ? "true" : "false",
      usb_device ? "true" : "false",
      static_cast<unsigned long>(receive_queue.depth(PROTO_UART)),
      static_cast<unsigned long>(receive_queue.depth(PROTO_CAN)),
      static_cast<unsigned long>(receive_queue.depth(PROTO_USB)),
      static_cast<unsigned long>(receive_queue.depth(PROTO_WIFI)),
      static_cast<unsigned long>(receive_queue.depth(PROTO_BLE)),
      static_cast<unsigned long>(receive_queue.depth(PROTO_GPIO)),
      static_cast<unsigned long>(receive_queue.overflow(PROTO_UART)),
      static_cast<unsigned long>(receive_queue.overflow(PROTO_CAN)),
      static_cast<unsigned long>(receive_queue.overflow(PROTO_USB)),
      static_cast<unsigned long>(receive_queue.overflow(PROTO_WIFI)),
      static_cast<unsigned long>(receive_queue.overflow(PROTO_BLE)),
      static_cast<unsigned long>(receive_queue.overflow(PROTO_GPIO)));
  appendText(metadata_buffer, sizeof(metadata_buffer), tail);
}

void configureUart(const TlvReader& reader, uint32_t request_id) {
  uint8_t port = 0, data_bits = 8, parity = 0, stop_bits = 1;
  uint32_t baudrate = 0;
  if (!reader.getU8(UART_PORT, port) || port > 3 ||
      !reader.getU32(BAUDRATE, baudrate) || baudrate < 50 ||
      baudrate > 4000000 || !reader.getU8(DATA_BITS, data_bits) ||
      (data_bits != 7 && data_bits != 8) || !reader.getU8(PARITY, parity) ||
      parity > 2 || !reader.getU8(STOP_BITS, stop_bits) ||
      (stop_bits != 1 && stop_bits != 2)) {
    sendError(request_id, kMalformed, "invalid UART configuration");
    return;
  }
  uint16_t config = SERIAL_8N1;
  if (data_bits == 7 && parity == 0) config = stop_bits == 2 ? SERIAL_7N2 : SERIAL_7N1;
  if (data_bits == 7 && parity == 1) config = stop_bits == 2 ? SERIAL_7E2 : SERIAL_7E1;
  if (data_bits == 7 && parity == 2) config = stop_bits == 2 ? SERIAL_7O2 : SERIAL_7O1;
  if (data_bits == 8 && parity == 0) config = stop_bits == 2 ? SERIAL_8N2 : SERIAL_8N1;
  if (data_bits == 8 && parity == 1) config = stop_bits == 2 ? SERIAL_8E2 : SERIAL_8E1;
  if (data_bits == 8 && parity == 2) config = stop_bits == 2 ? SERIAL_8O2 : SERIAL_8O1;
  switch (port) {
    case 0: Serial1.begin(baudrate, config); break;
    case 1: Serial2.begin(baudrate, config); break;
    case 2: Serial3.begin(baudrate, config); break;
    case 3: Serial4.begin(baudrate, config); break;
  }
  uart_configured[port] = true;
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"port\":%u,\"baudrate\":%lu}", port,
           static_cast<unsigned long>(baudrate));
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

CanBitRate canBitrate(uint32_t bitrate) {
  switch (bitrate) {
    case 125000: return CanBitRate::BR_125k;
    case 250000: return CanBitRate::BR_250k;
    case 500000: return CanBitRate::BR_500k;
    default: return CanBitRate::BR_1000k;
  }
}

void configureCan(const TlvReader& reader, uint32_t request_id) {
  uint8_t bus = 0;
  uint32_t bitrate = 0;
  if (!reader.getU8(BUS, bus) || bus > 1 ||
      !reader.getU32(CAN_BITRATE, bitrate) ||
      (bitrate != 125000 && bitrate != 250000 && bitrate != 500000 &&
       bitrate != 1000000)) {
    sendError(request_id, kMalformed, "invalid classic CAN configuration");
    return;
  }
  const bool ok = bus == 0 ? CAN.begin(canBitrate(bitrate))
                           : CAN1.begin(canBitrate(bitrate));
  if (!ok) {
    sendError(request_id, kIoError, "CAN controller did not initialize");
    return;
  }
  can_configured[bus] = true;
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"bus\":%u,\"bitrate\":%lu,\"classic_can\":true,"
           "\"external_transceiver_required\":true}",
           bus, static_cast<unsigned long>(bitrate));
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void spiExchange(const TlvReader& reader, uint32_t request_id) {
  uint8_t bus = 0, mode = 0, bit_order = 0, fill = 0xFF;
  uint16_t chip_select = 0;
  uint32_t clock = 0, requested_read = 0;
  const uint8_t* write_data = nullptr;
  uint16_t write_length = 0;
  if (!reader.getU8(BUS, bus) || bus > 1 ||
      !reader.getU16(CHIP_SELECT, chip_select) ||
      !reader.getU32(CLOCK_HZ, clock) || clock < 1000 || clock > 50000000 ||
      !reader.getU8(MODE, mode) || mode > 3 ||
      !reader.getU8(BIT_ORDER, bit_order) || bit_order > 1 ||
      !reader.getU8(FILL_BYTE, fill) ||
      !reader.getU32(READ_LENGTH, requested_read) ||
      !reader.bytes(DATA, write_data, write_length, false)) {
    sendError(request_id, kMalformed, "invalid SPI exchange");
    return;
  }
  const size_t clocks = max<size_t>(write_length, requested_read);
  if (clocks > kMaxApplicationTransfer ||
      !pin_resources.claim(chip_select,
                           bus == 0 ? kResourceOwnerSpi0 : kResourceOwnerSpi1)) {
    sendError(request_id, kConflict, "SPI chip-select is unsafe or already owned");
    return;
  }
  auto& spi = bus == 0 ? SPI : SPI1;
  spi.begin();
  pinMode(chip_select, OUTPUT);
  digitalWrite(chip_select, HIGH);
  spi.beginTransaction(SPISettings(clock, bit_order ? LSBFIRST : MSBFIRST, mode));
  digitalWrite(chip_select, LOW);
  for (size_t index = 0; index < clocks; ++index) {
    response_data[index] = spi.transfer(index < write_length ? write_data[index] : fill);
  }
  digitalWrite(chip_select, HIGH);
  spi.endTransaction();
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"bus\":%u,\"chip_select\":\"D%u\",\"clock_hz\":%lu,"
           "\"mode\":%u}",
           bus, chip_select, static_cast<unsigned long>(clock), mode);
  sendResponse(request_id, kStatusOk, response_data,
               static_cast<uint16_t>(clocks), metadata_buffer);
}

void i2cExchange(const TlvReader& reader, uint32_t request_id) {
  uint8_t bus = 0, address = 0, repeated = 1;
  uint32_t clock = 0, requested_read = 0;
  const uint8_t* write_data = nullptr;
  uint16_t write_length = 0;
  if (!reader.getU8(BUS, bus) || bus > 1 ||
      !reader.getU8(I2C_ADDRESS, address) || address < 0x08 || address > 0x77 ||
      !reader.getU32(CLOCK_HZ, clock) || (clock != 100000 && clock != 400000) ||
      !reader.getU32(READ_LENGTH, requested_read) || requested_read > 32 ||
      !reader.getU8(REPEATED_START, repeated) || repeated > 1 ||
      !reader.bytes(DATA, write_data, write_length, false) || write_length > 32) {
    sendError(request_id, kMalformed, "invalid I2C transaction");
    return;
  }
  auto& wire = bus == 0 ? Wire : Wire2;  // Wire1 is reserved for ATECC608A.
  wire.begin();
  wire.setClock(clock);
  if (write_length) {
    wire.beginTransmission(address);
    if (wire.write(write_data, write_length) != write_length ||
        wire.endTransmission(requested_read && repeated ? false : true) != 0) {
      sendError(request_id, kIoError, "I2C write failed");
      return;
    }
  }
  uint16_t received = 0;
  if (requested_read) {
    wire.requestFrom(address, static_cast<size_t>(requested_read), true);
    while (wire.available() && received < requested_read) {
      response_data[received++] = static_cast<uint8_t>(wire.read());
    }
    if (received != requested_read) {
      sendError(request_id, kIoError, "I2C read returned fewer bytes than requested");
      return;
    }
  }
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"bus\":%u,\"address\":%u,\"clock_hz\":%lu,"
           "\"reserved_bus\":\"Wire1\"}",
           bus, address, static_cast<unsigned long>(clock));
  sendResponse(request_id, kStatusOk, response_data, received, metadata_buffer);
}

size_t uartWrite(uint8_t port, const uint8_t* data, size_t length) {
  switch (port) {
    case 0: return Serial1.write(data, length);
    case 1: return Serial2.write(data, length);
    case 2: return Serial3.write(data, length);
    case 3: return Serial4.write(data, length);
    default: return 0;
  }
}

void uartWriteOperation(const TlvReader& reader, uint32_t request_id) {
  uint8_t port = 0;
  const uint8_t* data = nullptr;
  uint16_t length = 0;
  if (!reader.getU8(UART_PORT, port) || port > 3 || !uart_configured[port] ||
      !reader.bytes(DATA, data, length)) {
    sendError(request_id, kMalformed, "UART is unconfigured or request is invalid");
    return;
  }
  if (uartWrite(port, data, length) != length) {
    sendError(request_id, kIoError, "UART write was incomplete");
    return;
  }
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"port\":%u,\"written\":%u}", port, length);
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void canSend(const TlvReader& reader, uint32_t request_id) {
  uint8_t bus = 0, extended = 0;
  uint32_t identifier = 0;
  const uint8_t* data = nullptr;
  uint16_t length = 0;
  if (!reader.getU8(BUS, bus) || bus > 1 || !can_configured[bus] ||
      !reader.getU32(CAN_ID, identifier) ||
      !reader.getU8(CAN_EXTENDED, extended) || extended > 1 ||
      (!extended && identifier > 0x7FF) || identifier > 0x1FFFFFFF ||
      !reader.bytes(DATA, data, length) || length > 8) {
    sendError(request_id, kMalformed, "invalid classic CAN frame");
    return;
  }
  CanMsg frame(extended ? CanExtendedId(identifier) : CanStandardId(identifier),
               static_cast<uint8_t>(length), data);
  const int result = bus == 0 ? CAN.write(frame) : CAN1.write(frame);
  if (result <= 0) {
    sendError(request_id, kIoError, "CAN frame could not be queued");
    return;
  }
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"bus\":%u,\"arbitration_id\":%lu,\"extended\":%s}",
           bus, static_cast<unsigned long>(identifier), extended ? "true" : "false");
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void gpioConfigure(const TlvReader& reader, uint32_t request_id) {
  uint16_t pin = 0;
  uint8_t mode = 0, pull = 0, initial = 0;
  if (!reader.getU16(PIN, pin) || !reader.getU8(GPIO_MODE, mode) || mode > 1 ||
      !reader.getU8(PULL, pull) || pull > 2 ||
      !reader.getU8(INITIAL_VALUE, initial, false) || initial > 1 ||
      !safeDynamicPin(pin) || (mode == 1 && pull != 0)) {
    sendError(request_id, kMalformed, "GPIO configuration is unsafe or malformed");
    return;
  }
  if (!pin_resources.claim(pin, kResourceOwnerGpio)) {
    sendError(request_id, kConflict, "GPIO pin is already owned");
    return;
  }
  if (mode == 0) {
    pinMode(pin, pull == 1 ? INPUT_PULLUP : pull == 2 ? INPUT_PULLDOWN : INPUT);
  } else {
    pinMode(pin, OUTPUT);
    digitalWrite(pin, initial ? HIGH : LOW);
  }
  gpio_configured[pin] = true;
  gpio_output[pin] = mode == 1;
  gpio_last[pin] = digitalRead(pin) ? 1 : 0;
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"pin\":\"D%u\",\"mode\":\"%s\"}", pin,
           mode ? "output" : "input");
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void gpioWatch(const TlvReader& reader, uint32_t request_id) {
  uint16_t pin = 0;
  uint8_t edge = 0;
  if (!reader.getU16(PIN, pin) || !reader.getU8(EDGE, edge) || edge > 3 ||
      !safeDynamicPin(pin) || !gpio_configured[pin]) {
    sendError(request_id, kMalformed, "GPIO watch requires a configured safe pin");
    return;
  }
  gpio_edge[pin] = edge;
  gpio_last[pin] = digitalRead(pin) ? 1 : 0;
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"pin\":\"D%u\",\"edge\":%u}", pin, edge);
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void gpioOperation(const TlvReader& reader, uint32_t request_id,
                   uint16_t operation) {
  uint16_t pin = 0;
  uint8_t value = 0;
  uint32_t duration = 0;
  if (!reader.getU16(PIN, pin) || !safeDynamicPin(pin) ||
      !gpio_configured[pin]) {
    sendError(request_id, kMalformed, "GPIO operation requires a configured safe pin");
    return;
  }
  if (operation == GPIO_READ) {
    response_data[0] = digitalRead(pin) ? 1 : 0;
    snprintf(metadata_buffer, sizeof(metadata_buffer), "{\"pin\":\"D%u\"}", pin);
    sendResponse(request_id, kStatusOk, response_data, 1, metadata_buffer);
    return;
  }
  if (!reader.getU8(GPIO_VALUE, value) || value > 1) {
    sendError(request_id, kMalformed, "invalid GPIO value");
    return;
  }
  if (!gpio_output[pin]) {
    sendError(request_id, kConflict, "GPIO write requires an output-owned pin");
    return;
  }
  if (operation == GPIO_PULSE) {
    if (!reader.getU32(DURATION_US, duration) || duration == 0 || duration > 1000000) {
      sendError(request_id, kMalformed, "invalid GPIO pulse duration");
      return;
    }
  }
  digitalWrite(pin, value ? HIGH : LOW);
  if (operation == GPIO_PULSE) {
    delayMicroseconds(duration);
    digitalWrite(pin, value ? LOW : HIGH);
  }
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"pin\":\"D%u\",\"value\":%s,\"duration_us\":%lu}", pin,
           value ? "true" : "false", static_cast<unsigned long>(duration));
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

class RawUsbEnumerator : public IUSBEnumerator {
 public:
  explicit RawUsbEnumerator(uint8_t selected_interface)
      : selected_interface_(selected_interface), hub_(false) {}
  void setVidPid(uint16_t, uint16_t) override {}
  bool parseInterface(uint8_t number, uint8_t intf_class, uint8_t, uint8_t) override {
    if (intf_class == HUB_CLASS) hub_ = true;
    return !hub_ && number == selected_interface_;
  }
  bool useEndpoint(uint8_t number, ENDPOINT_TYPE type,
                   ENDPOINT_DIRECTION) override {
    return number == selected_interface_ && type != ISOCHRONOUS_ENDPOINT;
  }
  bool hub() const { return hub_; }

 private:
  uint8_t selected_interface_;
  bool hub_;
};

bool usbSerial(USBDeviceConnected* device, char* output, size_t capacity) {
  uint8_t descriptor[18] = {};
  if (!usb_host ||
      usb_host->controlRead(device, 0x80, GET_DESCRIPTOR,
                            DEVICE_DESCRIPTOR << 8, 0, descriptor,
                            sizeof(descriptor)) != USB_TYPE_OK ||
      descriptor[16] == 0) {
    output[0] = '\0';
    return false;
  }
  uint8_t language[4] = {};
  if (usb_host->controlRead(device, 0x80, GET_DESCRIPTOR, 3 << 8, 0, language,
                            sizeof(language)) != USB_TYPE_OK) {
    output[0] = '\0';
    return false;
  }
  const uint16_t language_id = language[2] | (language[3] << 8);
  uint8_t serial[126] = {};
  if (usb_host->controlRead(device, 0x80, GET_DESCRIPTOR,
                            (3 << 8) | descriptor[16], language_id, serial,
                            sizeof(serial)) != USB_TYPE_OK || serial[0] < 2) {
    output[0] = '\0';
    return false;
  }
  size_t written = 0;
  for (size_t index = 2; index + 1 < serial[0] && written + 1 < capacity; index += 2) {
    if (serial[index + 1] != 0 || serial[index] < 0x20 || serial[index] > 0x7E) {
      output[0] = '\0';
      return false;
    }
    output[written++] = static_cast<char>(serial[index]);
  }
  output[written] = '\0';
  return written > 0;
}

void usbEnumerate(uint32_t request_id) {
  usb_host = USBHost::getHostInst();
  metadata_buffer[0] = '\0';
  appendText(metadata_buffer, sizeof(metadata_buffer), "{\"devices\":[");
  bool first = true;
  for (uint8_t index = 0; index < MAX_DEVICE_CONNECTED; ++index) {
    USBDeviceConnected* device = usb_host->getDevice(index);
    if (!device || device->getClass() == HUB_CLASS) continue;
    char entry[180];
    snprintf(entry, sizeof(entry),
             "%s{\"index\":%u,\"vid\":%u,\"pid\":%u,"
             "\"interfaces\":%u,\"hub\":false}",
             first ? "" : ",", index, device->getVid(), device->getPid(),
             device->getNbIntf());
    appendText(metadata_buffer, sizeof(metadata_buffer), entry);
    first = false;
  }
  appendText(metadata_buffer, sizeof(metadata_buffer), "]}");
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void usbSelect(const TlvReader& reader, uint32_t request_id) {
  uint16_t vid = 0, pid = 0;
  uint8_t interface_number = 0;
  const uint8_t* serial_value = nullptr;
  uint16_t serial_length = 0;
  if (!reader.getU16(VID, vid) || !reader.getU16(PID, pid) ||
      !reader.getU8(USB_INTERFACE, interface_number, false) ||
      !reader.bytes(SERIAL_NUMBER, serial_value, serial_length, false) ||
      serial_length > 126) {
    sendError(request_id, kMalformed, "invalid USB selector");
    return;
  }
  usb_host = USBHost::getHostInst();
  USBDeviceConnected* match = nullptr;
  uint8_t matches = 0;
  char observed_serial[127] = {};
  for (uint8_t index = 0; index < MAX_DEVICE_CONNECTED; ++index) {
    USBDeviceConnected* device = usb_host->getDevice(index);
    if (!device || device->getClass() == HUB_CLASS || device->getVid() != vid ||
        device->getPid() != pid) continue;
    if (serial_value) {
      if (!usbSerial(device, observed_serial, sizeof(observed_serial)) ||
          strlen(observed_serial) != serial_length ||
          memcmp(observed_serial, serial_value, serial_length) != 0) {
        continue;
      }
    }
    match = device;
    ++matches;
  }
  if (matches != 1 || !match) {
    sendError(request_id, kSelectionError, "USB selector is absent or ambiguous");
    return;
  }
  RawUsbEnumerator enumerator(interface_number);
  if (usb_host->enumerate(match, &enumerator) != USB_TYPE_OK || enumerator.hub()) {
    sendError(request_id, kUnsupported, "USB interface is a hub or could not enumerate");
    return;
  }
  usb_device = match;
  usb_interface = interface_number;
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"vid\":%u,\"pid\":%u,\"interface\":%u,"
           "\"serial_matched\":%s,\"endpoints\":[",
           vid, pid, interface_number, serial_value ? "true" : "false");
  bool first = true;
  for (uint8_t index = 0; index < MAX_ENDPOINT_PER_INTERFACE; ++index) {
    USBEndpoint* endpoint = match->getEndpoint(interface_number, index);
    if (!endpoint || endpoint->getType() == ISOCHRONOUS_ENDPOINT) continue;
    const char* type = endpoint->getType() == CONTROL_ENDPOINT
                           ? "control"
                           : endpoint->getType() == BULK_ENDPOINT
                                 ? "bulk"
                                 : endpoint->getType() == INTERRUPT_ENDPOINT
                                       ? "interrupt"
                                       : "unknown";
    char entry[120];
    snprintf(entry, sizeof(entry),
             "%s{\"address\":%u,\"type\":\"%s\",\"max_packet\":%lu}",
             first ? "" : ",", endpoint->getAddress(), type,
             static_cast<unsigned long>(endpoint->getSize()));
    appendText(metadata_buffer, sizeof(metadata_buffer), entry);
    first = false;
  }
  appendText(metadata_buffer, sizeof(metadata_buffer), "]}");
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

USBEndpoint* selectedEndpoint(uint8_t address, ENDPOINT_TYPE type) {
  if (!usb_device) return nullptr;
  for (uint8_t index = 0; index < MAX_ENDPOINT_PER_INTERFACE; ++index) {
    USBEndpoint* endpoint = usb_device->getEndpoint(usb_interface, index);
    if (endpoint && endpoint->getType() == type && endpoint->getAddress() == address) {
      return endpoint;
    }
  }
  return nullptr;
}

void usbTransfer(const TlvReader& reader, uint32_t request_id) {
  uint8_t type = 0, endpoint_address = 0;
  uint32_t read_length = 0;
  const uint8_t* write_data = nullptr;
  uint16_t write_length = 0;
  if (!usb_device || !reader.getU8(TRANSFER_TYPE, type) || type > 2 ||
      !reader.getU8(ENDPOINT, endpoint_address) ||
      !reader.getU32(READ_LENGTH, read_length) ||
      read_length > kMaxApplicationTransfer ||
      (type == 2 && read_length > 4096) ||
      !reader.bytes(DATA, write_data, write_length, false)) {
    sendError(request_id, kMalformed, "invalid USB transfer or no selected device");
    return;
  }
  USB_TYPE status = USB_TYPE_ERROR;
  uint16_t returned = 0;
  if (type == 0) {
    uint8_t request_type = 0, request_code = 0;
    uint16_t value = 0, index = 0;
    if (endpoint_address != 0 || !reader.getU8(REQUEST_TYPE, request_type) ||
        !reader.getU8(REQUEST_CODE, request_code) ||
        !reader.getU16(VALUE, value) || !reader.getU16(INDEX, index)) {
      sendError(request_id, kMalformed, "USB control setup packet is incomplete");
      return;
    }
    if (request_type & 0x80) {
      status = usb_host->controlRead(usb_device, request_type, request_code,
                                     value, index, response_data, read_length);
      returned = status == USB_TYPE_OK ? static_cast<uint16_t>(read_length) : 0;
    } else {
      status = usb_host->controlWrite(usb_device, request_type, request_code,
                                      value, index,
                                      const_cast<uint8_t*>(write_data), write_length);
    }
  } else {
    const ENDPOINT_TYPE endpoint_type = type == 1 ? BULK_ENDPOINT : INTERRUPT_ENDPOINT;
    USBEndpoint* endpoint = selectedEndpoint(endpoint_address, endpoint_type);
    if (!endpoint || endpoint->getType() == ISOCHRONOUS_ENDPOINT) {
      sendError(request_id, kUnsupported, "USB endpoint is absent or unsupported");
      return;
    }
    const bool input = (endpoint_address & 0x80) != 0;
    if (input) {
      status = type == 1
                   ? usb_host->bulkRead(usb_device, endpoint, response_data, read_length)
                   : usb_host->interruptRead(usb_device, endpoint, response_data, read_length);
      returned = status == USB_TYPE_OK
                     ? static_cast<uint16_t>(min<int>(
                           read_length, max(0, endpoint->getLengthTransferred())))
                     : 0;
      if (returned) {
        receive_queue.push(PROTO_USB, endpoint_address, response_data, returned,
                           timestampUs());
      }
    } else {
      status = type == 1
                   ? usb_host->bulkWrite(usb_device, endpoint,
                                         const_cast<uint8_t*>(write_data), write_length)
                   : usb_host->interruptWrite(usb_device, endpoint,
                                              const_cast<uint8_t*>(write_data),
                                              write_length);
    }
  }
  if (status != USB_TYPE_OK) {
    sendError(request_id, kIoError, "USB transfer failed");
    return;
  }
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"transfer_type\":%u,\"endpoint\":%u,\"usb_status\":%u}",
           type, endpoint_address, static_cast<unsigned>(status));
  sendResponse(request_id, kStatusOk, response_data, returned, metadata_buffer);
}

void wifiConnect(const TlvReader& reader, uint32_t request_id) {
  if (radio_mode == 2) {
    sendError(request_id, kConflict, "Wi-Fi and BLE sessions are mutually exclusive");
    return;
  }
  char ssid[33] = {};
  char password[64] = {};
  if (!copyString(reader, SSID, ssid, sizeof(ssid)) ||
      !copyString(reader, PASSWORD, password, sizeof(password))) {
    sendError(request_id, kMalformed, "wireless profile is invalid");
    return;
  }
  radio_mode = 1;
  const int result = WiFi.begin(ssid, password);
  memset(ssid, 0, sizeof(ssid));
  memset(password, 0, sizeof(password));
  const uint32_t deadline = millis() + 30000;
  while (WiFi.status() != WL_CONNECTED && static_cast<int32_t>(deadline - millis()) > 0) {
    delay(50);
  }
  wifi_connected = result == WL_CONNECTED || WiFi.status() == WL_CONNECTED;
  if (!wifi_connected) {
    WiFi.disconnect();
    radio_mode = 0;
    sendError(request_id, kIoError, "Wi-Fi connection failed");
    return;
  }
  sendResponse(request_id, kStatusOk, nullptr, 0,
               "{\"connected\":true,\"credentials_volatile\":true}");
}

void wifiDisconnect(uint32_t request_id) {
  for (uint8_t index = 0; index < 8; ++index) {
    if (socket_used[index]) {
      tcp_clients[index].stop();
      udp_sockets[index].stop();
      socket_used[index] = false;
    }
  }
  WiFi.disconnect();
  wifi_connected = false;
  radio_mode = 0;
  sendResponse(request_id, kStatusOk, nullptr, 0, "{\"connected\":false}");
}

void wifiSocketOpen(const TlvReader& reader, uint32_t request_id) {
  uint8_t protocol = 0;
  uint16_t port = 0, local_port = 0;
  char host[254] = {};
  if (!wifi_connected || !reader.getU8(SOCKET_PROTOCOL, protocol) || protocol > 1 ||
      !reader.getU16(NETWORK_PORT, port) || port == 0 ||
      !reader.getU16(LOCAL_PORT, local_port, false) ||
      !copyString(reader, HOST, host, sizeof(host))) {
    sendError(request_id, kMalformed, "invalid Wi-Fi socket request");
    return;
  }
  int selected = -1;
  for (uint8_t index = 0; index < 8; ++index) {
    if (!socket_used[index]) { selected = index; break; }
  }
  if (selected < 0) {
    sendError(request_id, kConflict, "Wi-Fi socket limit reached");
    return;
  }
  bool ok = protocol == 0 ? tcp_clients[selected].connect(host, port)
                          : udp_sockets[selected].begin(local_port ? local_port
                                                                  : 49152 + selected);
  if (!ok) {
    sendError(request_id, kIoError, "Wi-Fi socket could not open");
    return;
  }
  socket_used[selected] = true;
  socket_protocol[selected] = protocol;
  strncpy(socket_host[selected], host, sizeof(socket_host[selected]) - 1);
  socket_port[selected] = port;
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"socket_id\":%d,\"protocol\":\"%s\"}", selected,
           protocol == 0 ? "tcp" : "udp");
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void wifiSocketClose(const TlvReader& reader, uint32_t request_id) {
  uint8_t socket = 0;
  if (!reader.getU8(SOCKET_ID, socket) || socket > 7 || !socket_used[socket]) {
    sendError(request_id, kMalformed, "unknown Wi-Fi socket");
    return;
  }
  tcp_clients[socket].stop();
  udp_sockets[socket].stop();
  socket_used[socket] = false;
  snprintf(metadata_buffer, sizeof(metadata_buffer), "{\"socket_id\":%u}", socket);
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void wifiSend(const TlvReader& reader, uint32_t request_id) {
  uint8_t socket = 0;
  const uint8_t* data = nullptr;
  uint16_t length = 0;
  if (!reader.getU8(SOCKET_ID, socket) || socket > 7 || !socket_used[socket] ||
      !reader.bytes(DATA, data, length)) {
    sendError(request_id, kMalformed, "invalid Wi-Fi send request");
    return;
  }
  size_t written = 0;
  if (socket_protocol[socket] == 0) {
    written = tcp_clients[socket].write(data, length);
  } else if (udp_sockets[socket].beginPacket(socket_host[socket], socket_port[socket])) {
    written = udp_sockets[socket].write(data, length);
    if (!udp_sockets[socket].endPacket()) written = 0;
  }
  if (written != length) {
    sendError(request_id, kIoError, "Wi-Fi send was incomplete");
    return;
  }
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"socket_id\":%u,\"written\":%u}", socket, length);
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void pairingDisplay(uint32_t code) { displayed_pairing_code = code % 1000000; }
bool pairingConfirm() {
  return expected_pairing_code == UINT32_MAX ||
         expected_pairing_code == displayed_pairing_code;
}

bool ensureBle(uint32_t request_id) {
  if (radio_mode == 1) {
    sendError(request_id, kConflict, "Wi-Fi and BLE sessions are mutually exclusive");
    return false;
  }
  if (!ble_initialized) {
    if (!BLE.begin()) {
      sendError(request_id, kIoError, "BLE controller did not initialize");
      return false;
    }
    BLE.setPairable(Pairable::ONCE);
    BLE.setDisplayCode(pairingDisplay);
    BLE.setBinaryConfirmPairing(pairingConfirm);
    ble_initialized = true;
  }
  radio_mode = 2;
  return true;
}

void bleScan(const TlvReader& reader, uint32_t request_id) {
  uint32_t duration = 3000;
  char uuid[37] = {};
  if (!reader.getU32(SCAN_DURATION_MS, duration) || duration < 100 ||
      duration > 30000 ||
      !copyString(reader, SERVICE_UUID, uuid, sizeof(uuid), false)) {
    sendError(request_id, kMalformed, "invalid BLE scan request");
    return;
  }
  if (!ensureBle(request_id)) return;
  const int started = uuid[0] ? BLE.scanForUuid(uuid) : BLE.scan();
  if (!started) {
    sendError(request_id, kIoError, "BLE scan did not start");
    return;
  }
  metadata_buffer[0] = '\0';
  appendText(metadata_buffer, sizeof(metadata_buffer), "{\"devices\":[");
  bool first = true;
  uint8_t count = 0;
  const uint32_t deadline = millis() + duration;
  while (static_cast<int32_t>(deadline - millis()) > 0 && count < 16) {
    BLEDevice device = BLE.available();
    if (!device) { BLE.poll(); delay(5); continue; }
    const String address = device.address();
    const String local_name = device.hasLocalName() ? device.localName() : "";
    char escaped_address[128] = {};
    char escaped_name[384] = {};
    if (!encodeJsonString(address.c_str(), escaped_address,
                          sizeof(escaped_address)) ||
        !encodeJsonString(local_name.c_str(), escaped_name,
                          sizeof(escaped_name))) {
      continue;
    }
    char item[640];
    const int item_length = snprintf(
        item, sizeof(item), "%s{\"address\":%s,\"name\":%s,\"rssi\":%d}",
        first ? "" : ",", escaped_address, escaped_name, device.rssi());
    const size_t current = strnlen(metadata_buffer, sizeof(metadata_buffer));
    if (item_length < 0 || static_cast<size_t>(item_length) >= sizeof(item) ||
        current + static_cast<size_t>(item_length) + 3 >=
            sizeof(metadata_buffer)) {
      break;
    }
    appendText(metadata_buffer, sizeof(metadata_buffer), item);
    first = false;
    ++count;
  }
  BLE.stopScan();
  appendText(metadata_buffer, sizeof(metadata_buffer), "]}");
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void bleConnect(const TlvReader& reader, uint32_t request_id) {
  char address[18] = {};
  if (!copyString(reader, BLE_ADDRESS, address, sizeof(address))) {
    sendError(request_id, kMalformed, "invalid BLE address");
    return;
  }
  if (!ensureBle(request_id)) return;
  if (!BLE.scanForAddress(address)) {
    sendError(request_id, kIoError, "BLE address scan did not start");
    return;
  }
  const uint32_t deadline = millis() + 10000;
  BLEDevice match;
  while (static_cast<int32_t>(deadline - millis()) > 0) {
    BLEDevice candidate = BLE.available();
    if (candidate && candidate.address().equalsIgnoreCase(address)) {
      match = candidate;
      break;
    }
    BLE.poll();
    delay(5);
  }
  BLE.stopScan();
  if (!match || !match.connect()) {
    sendError(request_id, kIoError, "BLE peer did not connect");
    return;
  }
  ble_peer = match;
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"address\":\"%s\",\"connected\":true}", address);
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void bleDisconnect(uint32_t request_id) {
  if (ble_peer && ble_peer.connected()) ble_peer.disconnect();
  ble_peer = BLEDevice();
  ble_subscription = BLECharacteristic();
  ble_subscribed = false;
  expected_pairing_code = UINT32_MAX;
  displayed_pairing_code = UINT32_MAX;
  BLE.end();
  ble_initialized = false;
  radio_mode = 0;
  sendResponse(request_id, kStatusOk, nullptr, 0,
               "{\"connected\":false,\"pairing_volatile\":true}");
}

void blePair(const TlvReader& reader, uint32_t request_id) {
  const uint8_t* passkey = nullptr;
  uint16_t length = 0;
  if (!ensureBle(request_id) || !reader.bytes(PASSKEY, passkey, length, false)) return;
  expected_pairing_code = UINT32_MAX;
  if (passkey) {
    if (length != 6) {
      sendError(request_id, kMalformed, "BLE passkey profile is invalid");
      return;
    }
    uint32_t value = 0;
    for (uint8_t index = 0; index < 6; ++index) {
      if (passkey[index] < '0' || passkey[index] > '9') {
        sendError(request_id, kMalformed, "BLE passkey profile is invalid");
        return;
      }
      value = value * 10 + passkey[index] - '0';
    }
    expected_pairing_code = value;
  }
  BLE.setPairable(Pairable::ONCE);
  sendResponse(request_id, kStatusOk, nullptr, 0,
               passkey ? "{\"pairing\":\"named_numeric_confirmation\","
                         "\"volatile\":true}"
                       : "{\"pairing\":\"just_works\",\"volatile\":true}");
}

void bleDiscover(const TlvReader& reader, uint32_t request_id) {
  char service_uuid[37] = {};
  if (!ble_peer || !ble_peer.connected() ||
      !copyString(reader, SERVICE_UUID, service_uuid, sizeof(service_uuid), false)) {
    sendError(request_id, kMalformed, "BLE peer is not connected");
    return;
  }
  const bool ok = service_uuid[0] ? ble_peer.discoverService(service_uuid)
                                  : ble_peer.discoverAttributes();
  if (!ok) {
    sendError(request_id, kIoError, "BLE attribute discovery failed");
    return;
  }
  metadata_buffer[0] = '\0';
  appendText(metadata_buffer, sizeof(metadata_buffer), "{\"services\":[");
  bool first = true;
  for (int index = 0; index < ble_peer.serviceCount(); ++index) {
    BLEService service = ble_peer.service(index);
    char escaped_uuid[224] = {};
    if (!encodeJsonString(service.uuid(), escaped_uuid, sizeof(escaped_uuid))) {
      continue;
    }
    char item[228];
    snprintf(item, sizeof(item), "%s%s", first ? "" : ",", escaped_uuid);
    appendText(metadata_buffer, sizeof(metadata_buffer), item);
    first = false;
  }
  appendText(metadata_buffer, sizeof(metadata_buffer), "],\"characteristics\":[");
  first = true;
  for (int index = 0; index < ble_peer.characteristicCount(); ++index) {
    BLECharacteristic characteristic = ble_peer.characteristic(index);
    char escaped_uuid[224] = {};
    if (!encodeJsonString(characteristic.uuid(), escaped_uuid,
                          sizeof(escaped_uuid))) {
      continue;
    }
    char item[228];
    snprintf(item, sizeof(item), "%s%s", first ? "" : ",", escaped_uuid);
    appendText(metadata_buffer, sizeof(metadata_buffer), item);
    first = false;
  }
  appendText(metadata_buffer, sizeof(metadata_buffer), "]}");
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

BLECharacteristic findBleCharacteristic(const char* service_uuid,
                                        const char* characteristic_uuid) {
  if (!ble_peer || !ble_peer.connected()) return BLECharacteristic();
  BLEService service = ble_peer.service(service_uuid);
  return service ? service.characteristic(characteristic_uuid)
                 : BLECharacteristic();
}

void bleSubscribe(const TlvReader& reader, uint32_t request_id) {
  char service_uuid[37] = {}, characteristic_uuid[37] = {};
  uint8_t enabled = 1;
  if (!copyString(reader, SERVICE_UUID, service_uuid, sizeof(service_uuid)) ||
      !copyString(reader, CHARACTERISTIC_UUID, characteristic_uuid,
                  sizeof(characteristic_uuid)) ||
      !reader.getU8(ENABLED, enabled) || enabled > 1) {
    sendError(request_id, kMalformed, "invalid BLE subscription request");
    return;
  }
  BLECharacteristic characteristic =
      findBleCharacteristic(service_uuid, characteristic_uuid);
  if (!characteristic || (enabled ? !characteristic.subscribe()
                                  : !characteristic.unsubscribe())) {
    sendError(request_id, kIoError, "BLE subscription operation failed");
    return;
  }
  if (enabled) {
    ble_subscription = characteristic;
    ble_subscribed = true;
  } else {
    ble_subscription = BLECharacteristic();
    ble_subscribed = false;
  }
  snprintf(metadata_buffer, sizeof(metadata_buffer), "{\"subscribed\":%s}",
           enabled ? "true" : "false");
  sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
}

void bleReadWrite(const TlvReader& reader, uint32_t request_id, bool write) {
  char service_uuid[37] = {}, characteristic_uuid[37] = {};
  if (!copyString(reader, SERVICE_UUID, service_uuid, sizeof(service_uuid)) ||
      !copyString(reader, CHARACTERISTIC_UUID, characteristic_uuid,
                  sizeof(characteristic_uuid))) {
    sendError(request_id, kMalformed, "invalid BLE characteristic selector");
    return;
  }
  BLECharacteristic characteristic =
      findBleCharacteristic(service_uuid, characteristic_uuid);
  if (!characteristic) {
    sendError(request_id, kSelectionError, "BLE characteristic was not discovered");
    return;
  }
  uint16_t returned = 0;
  if (write) {
    uint8_t mode = 0;
    const uint8_t* data = nullptr;
    uint16_t length = 0;
    if (!reader.getU8(WRITE_MODE, mode) || mode > 1 ||
        !reader.bytes(DATA, data, length) ||
        !characteristic.writeValue(data, length, mode == 0)) {
      sendError(request_id, kIoError, "BLE characteristic write failed");
      return;
    }
  } else {
    const int result = characteristic.readValue(response_data,
                                                 kMaxApplicationTransfer);
    if (result < 0) {
      sendError(request_id, kIoError, "BLE characteristic read failed");
      return;
    }
    returned = static_cast<uint16_t>(result);
  }
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"service_uuid\":\"%s\",\"characteristic_uuid\":\"%s\"}",
           service_uuid, characteristic_uuid);
  sendResponse(request_id, kStatusOk, response_data, returned, metadata_buffer);
}

void pollUarts();
void pollCan();
void pollGpio();
void pollWifi();
void pollBle();

void receiveOperation(const TlvReader& reader, uint32_t request_id) {
  uint8_t protocol = 0, channel = 0, drain = 1;
  uint16_t limit = 4096;
  uint32_t timeout = 0;
  if (!reader.getU8(PROTOCOL, protocol) || protocol < PROTO_UART ||
      protocol > PROTO_GPIO || !reader.getU8(CHANNEL, channel) ||
      !reader.getU16(LIMIT, limit) || limit == 0 || limit > kMaxApplicationTransfer ||
      !reader.getU8(DRAIN, drain) || drain > 1 ||
      !reader.getU32(TIMEOUT_MS, timeout) || timeout > 300000) {
    sendError(request_id, kMalformed, "invalid receive request");
    return;
  }
  const uint32_t deadline = millis() + timeout;
  while (receive_queue.depth(protocol, channel) == 0 &&
         static_cast<int32_t>(deadline - millis()) > 0) {
    pollUarts();
    pollCan();
    pollGpio();
    pollWifi();
    pollBle();
    delay(1);
  }
  const size_t length = receive_queue.read(
      protocol, channel, response_data, limit, drain != 0);
  snprintf(metadata_buffer, sizeof(metadata_buffer),
           "{\"protocol\":%u,\"channel\":%u,"
           "\"record_header\":\"channel:u8,length:u16,timestamp_us:u64\","
           "\"drained\":%s}",
           protocol, channel, drain ? "true" : "false");
  sendResponse(request_id, kStatusOk, response_data, static_cast<uint16_t>(length),
               metadata_buffer,
               min<uint32_t>(receive_queue.depth(protocol, channel), UINT16_MAX),
               receive_queue.overflow(protocol));
}

void dispatch(uint32_t request_id, const uint8_t* body, size_t length) {
  TlvReader reader(body, length);
  uint16_t operation = 0;
  if (!reader.valid() || !reader.getU16(OPERATION, operation) ||
      !reader.onlyKnown([operation](uint16_t field) {
        return knownField(operation, field);
      })) {
    sendError(request_id, kMalformed, "malformed or unknown bridge TLV");
    return;
  }
  switch (operation) {
    case GET_STATUS:
      buildStatusMetadata();
      sendResponse(request_id, kStatusOk, nullptr, 0, metadata_buffer);
      break;
    case RECEIVE: receiveOperation(reader, request_id); break;
    case UART_CONFIGURE: configureUart(reader, request_id); break;
    case CAN_CONFIGURE: configureCan(reader, request_id); break;
    case USB_ENUMERATE: usbEnumerate(request_id); break;
    case USB_SELECT: usbSelect(reader, request_id); break;
    case USB_RESET:
      if (!usb_device || usb_host->resetDevice(usb_device) != USB_TYPE_OK)
        sendError(request_id, kIoError, "USB device reset failed");
      else
        sendResponse(request_id, kStatusOk, nullptr, 0, "{\"reset\":true}");
      break;
    case USB_RELEASE:
      usb_device = nullptr;
      sendResponse(request_id, kStatusOk, nullptr, 0, "{\"released\":true}");
      break;
    case WIFI_CONNECT: wifiConnect(reader, request_id); break;
    case WIFI_DISCONNECT: wifiDisconnect(request_id); break;
    case WIFI_SOCKET_OPEN: wifiSocketOpen(reader, request_id); break;
    case WIFI_SOCKET_CLOSE: wifiSocketClose(reader, request_id); break;
    case BLE_SCAN: bleScan(reader, request_id); break;
    case BLE_CONNECT: bleConnect(reader, request_id); break;
    case BLE_DISCONNECT: bleDisconnect(request_id); break;
    case BLE_PAIR: blePair(reader, request_id); break;
    case BLE_DISCOVER: bleDiscover(reader, request_id); break;
    case BLE_SUBSCRIBE: bleSubscribe(reader, request_id); break;
    case GPIO_CONFIGURE: gpioConfigure(reader, request_id); break;
    case GPIO_WATCH: gpioWatch(reader, request_id); break;
    case SPI_EXCHANGE: spiExchange(reader, request_id); break;
    case I2C_EXCHANGE: i2cExchange(reader, request_id); break;
    case UART_WRITE: uartWriteOperation(reader, request_id); break;
    case CAN_SEND: canSend(reader, request_id); break;
    case USB_TRANSFER: usbTransfer(reader, request_id); break;
    case WIFI_SEND: wifiSend(reader, request_id); break;
    case BLE_READ: bleReadWrite(reader, request_id, false); break;
    case BLE_WRITE: bleReadWrite(reader, request_id, true); break;
    case GPIO_READ:
    case GPIO_WRITE:
    case GPIO_PULSE: gpioOperation(reader, request_id, operation); break;
    default: sendError(request_id, kUnsupported, "unknown bridge operation"); break;
  }
}

void consumeFrame() {
  const size_t decoded = cobsDecode(encoded_input, encoded_length, decoded_frame,
                                    sizeof(decoded_frame));
  encoded_length = 0;
  if (decoded < sizeof(FrameHeader)) return;
  FrameHeader header{};
  memcpy(&header, decoded_frame, sizeof(header));
  if (memcmp(header.magic, kMagic, sizeof(kMagic)) != 0 ||
      header.wire_version != kWireVersion || header.message_type != REQUEST ||
      header.segment_count == 0 || header.segment_index >= header.segment_count ||
      header.payload_length != decoded - sizeof(header) ||
      frameCrc(header, decoded_frame + sizeof(header)) != header.crc32) {
    sendError(header.request_id, kMalformed, "invalid bridge frame");
    clearAssembly();
    return;
  }
  if (header.segment_index == 0) {
    clearAssembly();
    assembly_request_id = header.request_id;
    assembly_segment_count = header.segment_count;
  }
  if (header.request_id != assembly_request_id ||
      header.segment_count != assembly_segment_count ||
      header.segment_index != assembly_next_segment ||
      header.payload_length > sizeof(message_assembly) - assembly_length) {
    sendError(header.request_id, kMalformed, "stale or out-of-order bridge segment");
    clearAssembly();
    return;
  }
  memcpy(message_assembly + assembly_length, decoded_frame + sizeof(header),
         header.payload_length);
  assembly_length += header.payload_length;
  ++assembly_next_segment;
  if (assembly_next_segment == assembly_segment_count) {
    dispatch(assembly_request_id, message_assembly, assembly_length);
    clearAssembly();
  }
}

void pollUarts() {
  struct UartEntry { bool configured; Stream* stream; };
  UartEntry entries[] = {{uart_configured[0], &Serial1}, {uart_configured[1], &Serial2},
                         {uart_configured[2], &Serial3}, {uart_configured[3], &Serial4}};
  uint8_t buffer[256];
  for (uint8_t port = 0; port < 4; ++port) {
    if (!entries[port].configured) continue;
    uint16_t length = 0;
    while (entries[port].stream->available() && length < sizeof(buffer)) {
      buffer[length++] = static_cast<uint8_t>(entries[port].stream->read());
    }
    if (length) receive_queue.push(PROTO_UART, port, buffer, length, timestampUs());
  }
}

void pollCan() {
  for (uint8_t bus = 0; bus < 2; ++bus) {
    if (!can_configured[bus]) continue;
    auto& controller = bus == 0 ? CAN : CAN1;
    while (controller.available()) {
      CanMsg message = controller.read();
      uint8_t record[14] = {};
      const uint32_t identifier = message.isExtendedId() ? message.getExtendedId()
                                                         : message.getStandardId();
      memcpy(record, &identifier, 4);
      record[4] = message.isExtendedId() ? 1 : 0;
      record[5] = message.data_length;
      memcpy(record + 6, message.data, message.data_length);
      receive_queue.push(PROTO_CAN, bus, record, 6 + message.data_length,
                         timestampUs());
    }
  }
}

void pollGpio() {
  for (uint16_t pin = 0; pin < 103; ++pin) {
    if (!gpio_edge[pin]) continue;
    const uint8_t value = digitalRead(pin) ? 1 : 0;
    if (value == gpio_last[pin]) continue;
    const bool rising = !gpio_last[pin] && value;
    const uint8_t edge = gpio_edge[pin];
    gpio_last[pin] = value;
    if (edge == 3 || (edge == 1 && rising) || (edge == 2 && !rising)) {
      uint8_t record[3] = {static_cast<uint8_t>(pin & 0xFF),
                           static_cast<uint8_t>(pin >> 8), value};
      receive_queue.push(PROTO_GPIO, static_cast<uint8_t>(pin), record,
                         sizeof(record), timestampUs());
    }
  }
}

void pollWifi() {
  if (!wifi_connected) return;
  uint8_t buffer[512];
  for (uint8_t socket = 0; socket < 8; ++socket) {
    if (!socket_used[socket]) continue;
    uint16_t length = 0;
    if (socket_protocol[socket] == 0) {
      while (tcp_clients[socket].available() && length < sizeof(buffer)) {
        buffer[length++] = static_cast<uint8_t>(tcp_clients[socket].read());
      }
    } else {
      const int packet = udp_sockets[socket].parsePacket();
      if (packet > 0) {
        length = static_cast<uint16_t>(udp_sockets[socket].read(
            buffer, min<int>(packet, sizeof(buffer))));
      }
    }
    if (length) receive_queue.push(PROTO_WIFI, socket, buffer, length, timestampUs());
  }
}

void pollBle() {
  if (!ble_initialized) return;
  BLE.poll();
  if (ble_subscribed && ble_subscription && ble_subscription.valueUpdated()) {
    const int length = ble_subscription.readValue(response_data, 512);
    if (length > 0) {
      receive_queue.push(PROTO_BLE, 0, response_data,
                         static_cast<uint16_t>(length), timestampUs());
    }
  }
  if (BLE.paired()) expected_pairing_code = UINT32_MAX;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  usb_host = USBHost::getHostInst();
}

void loop() {
  while (Serial.available()) {
    const int value = Serial.read();
    if (value < 0) break;
    if (value == 0) {
      if (encoded_length) consumeFrame();
      continue;
    }
    if (encoded_length >= sizeof(encoded_input)) {
      encoded_length = 0;
      clearAssembly();
      continue;
    }
    encoded_input[encoded_length++] = static_cast<uint8_t>(value);
  }
  pollUarts();
  pollCan();
  pollGpio();
  pollWifi();
  pollBle();
}
