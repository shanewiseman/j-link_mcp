#pragma once

#include <Arduino.h>

namespace bridge {

constexpr uint8_t kWireVersion = 1;
constexpr size_t kMaxDecodedFrame = 4096;
constexpr size_t kMaxMessage = 64 * 1024;
constexpr uint8_t kMagic[4] = {'J', 'L', 'P', 'B'};

enum MessageType : uint8_t { REQUEST = 1, RESPONSE = 2, ERROR_RESPONSE = 3 };

enum Operation : uint16_t {
  GET_STATUS = 1,
  RECEIVE = 2,
  UART_CONFIGURE = 10,
  CAN_CONFIGURE = 11,
  USB_ENUMERATE = 12,
  USB_SELECT = 13,
  USB_RESET = 14,
  USB_RELEASE = 15,
  WIFI_CONNECT = 16,
  WIFI_DISCONNECT = 17,
  WIFI_SOCKET_OPEN = 18,
  WIFI_SOCKET_CLOSE = 19,
  BLE_SCAN = 20,
  BLE_CONNECT = 21,
  BLE_DISCONNECT = 22,
  BLE_PAIR = 23,
  BLE_DISCOVER = 24,
  BLE_SUBSCRIBE = 25,
  GPIO_CONFIGURE = 26,
  GPIO_WATCH = 27,
  SPI_EXCHANGE = 40,
  I2C_EXCHANGE = 41,
  UART_WRITE = 42,
  CAN_SEND = 43,
  USB_TRANSFER = 44,
  WIFI_SEND = 45,
  BLE_READ = 46,
  BLE_WRITE = 47,
  GPIO_READ = 48,
  GPIO_WRITE = 49,
  GPIO_PULSE = 50,
};

enum FieldId : uint16_t {
  OPERATION = 1,
  PROTOCOL = 2,
  BUS = 3,
  UART_PORT = 4,
  DATA = 5,
  READ_LENGTH = 6,
  CLOCK_HZ = 7,
  MODE = 8,
  BIT_ORDER = 9,
  CHIP_SELECT = 10,
  FILL_BYTE = 11,
  I2C_ADDRESS = 12,
  REPEATED_START = 13,
  BAUDRATE = 14,
  DATA_BITS = 15,
  PARITY = 16,
  STOP_BITS = 17,
  CAN_BITRATE = 18,
  CAN_ID = 19,
  CAN_EXTENDED = 20,
  VID = 21,
  PID = 22,
  SERIAL_NUMBER = 23,
  USB_INTERFACE = 24,
  ENDPOINT = 25,
  TRANSFER_TYPE = 26,
  REQUEST_TYPE = 27,
  REQUEST_CODE = 28,
  VALUE = 29,
  INDEX = 30,
  PROFILE = 31,
  HOST = 32,
  NETWORK_PORT = 33,
  SOCKET_ID = 34,
  SOCKET_PROTOCOL = 35,
  BLE_ADDRESS = 36,
  SERVICE_UUID = 37,
  CHARACTERISTIC_UUID = 38,
  WRITE_MODE = 39,
  PIN = 40,
  GPIO_MODE = 41,
  GPIO_VALUE = 42,
  PULL = 43,
  EDGE = 44,
  DURATION_US = 45,
  LIMIT = 46,
  DRAIN = 47,
  TIMEOUT_MS = 48,
  CHANNEL = 49,
  SCAN_DURATION_MS = 50,
  PASSKEY = 51,
  SSID = 52,
  PASSWORD = 53,
  LOCAL_PORT = 54,
  ENABLED = 55,
  INITIAL_VALUE = 56,
  STATUS = 0x8001,
  RESPONSE_DATA = 0x8002,
  METADATA_JSON = 0x8003,
  QUEUE_DEPTH = 0x8004,
  OVERFLOW_COUNT = 0x8005,
  TIMESTAMP_US = 0x8006,
  ERROR_MESSAGE = 0x8007,
};

#pragma pack(push, 1)
struct FrameHeader {
  uint8_t magic[4];
  uint8_t wire_version;
  uint8_t message_type;
  uint32_t request_id;
  uint16_t segment_index;
  uint16_t segment_count;
  uint16_t payload_length;
  uint32_t crc32;
};

struct TlvHeader {
  uint16_t type;
  uint16_t length;
};
#pragma pack(pop)

static_assert(sizeof(FrameHeader) == 20, "bridge frame header layout drift");
static_assert(sizeof(TlvHeader) == 4, "bridge TLV header layout drift");

inline uint32_t crc32(const uint8_t* data, size_t length, uint32_t seed = 0) {
  uint32_t crc = ~seed;
  for (size_t index = 0; index < length; ++index) {
    crc ^= data[index];
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ (0xEDB88320UL & (0UL - (crc & 1UL)));
    }
  }
  return ~crc;
}

inline uint32_t frameCrc(const FrameHeader& header, const uint8_t* payload) {
  FrameHeader copy = header;
  copy.crc32 = 0;
  uint32_t crc = crc32(reinterpret_cast<const uint8_t*>(&copy),
                       offsetof(FrameHeader, crc32));
  return crc32(payload, header.payload_length, crc);
}

inline size_t cobsEncode(const uint8_t* input, size_t length, uint8_t* output,
                         size_t capacity) {
  if (capacity == 0) return 0;
  size_t read = 0;
  size_t write = 1;
  size_t code_index = 0;
  uint8_t code = 1;
  while (read < length) {
    if (input[read] == 0) {
      if (code_index >= capacity) return 0;
      output[code_index] = code;
      code_index = write++;
      code = 1;
      ++read;
    } else {
      if (write >= capacity) return 0;
      output[write++] = input[read++];
      if (++code == 0xFF) {
        output[code_index] = code;
        code_index = write++;
        code = 1;
      }
    }
  }
  if (code_index >= capacity) return 0;
  output[code_index] = code;
  return write;
}

inline size_t cobsDecode(const uint8_t* input, size_t length, uint8_t* output,
                         size_t capacity) {
  size_t read = 0;
  size_t write = 0;
  while (read < length) {
    const uint8_t code = input[read++];
    if (code == 0 || read + code - 1 > length) return 0;
    for (uint8_t index = 1; index < code; ++index) {
      if (write >= capacity) return 0;
      output[write++] = input[read++];
    }
    if (code != 0xFF && read < length) {
      if (write >= capacity) return 0;
      output[write++] = 0;
    }
  }
  return write;
}

class TlvReader {
 public:
  TlvReader(const uint8_t* data, size_t length) : data_(data), length_(length) {}

  bool valid() const {
    size_t offset = 0;
    while (offset < length_) {
      if (length_ - offset < sizeof(TlvHeader)) return false;
      const auto* header = reinterpret_cast<const TlvHeader*>(data_ + offset);
      size_t prior = 0;
      while (prior < offset) {
        const auto* previous =
            reinterpret_cast<const TlvHeader*>(data_ + prior);
        if (previous->type == header->type) return false;
        prior += sizeof(TlvHeader) + previous->length;
      }
      offset += sizeof(TlvHeader);
      if (header->length > length_ - offset) return false;
      offset += header->length;
    }
    return offset == length_;
  }

  bool find(uint16_t type, const uint8_t*& value, uint16_t& length) const {
    size_t offset = 0;
    bool found = false;
    while (offset + sizeof(TlvHeader) <= length_) {
      const auto* header = reinterpret_cast<const TlvHeader*>(data_ + offset);
      offset += sizeof(TlvHeader);
      if (header->length > length_ - offset) return false;
      if (header->type == type) {
        if (found) return false;
        found = true;
        value = data_ + offset;
        length = header->length;
      }
      offset += header->length;
    }
    return found;
  }

  bool getU8(uint16_t type, uint8_t& result, bool required = true) const {
    const uint8_t* value = nullptr;
    uint16_t length = 0;
    if (!find(type, value, length)) return !required;
    if (length != 1) return false;
    result = value[0];
    return true;
  }

  bool getU16(uint16_t type, uint16_t& result, bool required = true) const {
    const uint8_t* value = nullptr;
    uint16_t length = 0;
    if (!find(type, value, length)) return !required;
    if (length != 2) return false;
    memcpy(&result, value, 2);
    return true;
  }

  bool getU32(uint16_t type, uint32_t& result, bool required = true) const {
    const uint8_t* value = nullptr;
    uint16_t length = 0;
    if (!find(type, value, length)) return !required;
    if (length != 4) return false;
    memcpy(&result, value, 4);
    return true;
  }

  bool bytes(uint16_t type, const uint8_t*& value, uint16_t& length,
             bool required = true) const {
    if (!find(type, value, length)) return !required;
    return true;
  }

  template <typename Predicate>
  bool onlyKnown(Predicate known) const {
    size_t offset = 0;
    while (offset + sizeof(TlvHeader) <= length_) {
      const auto* header = reinterpret_cast<const TlvHeader*>(data_ + offset);
      offset += sizeof(TlvHeader);
      if (header->length > length_ - offset || !known(header->type)) return false;
      offset += header->length;
    }
    return offset == length_;
  }

 private:
  const uint8_t* data_;
  size_t length_;
};

class TlvWriter {
 public:
  TlvWriter(uint8_t* data, size_t capacity)
      : data_(data), capacity_(capacity), length_(0) {}

  bool append(uint16_t type, const void* value, uint16_t length) {
    if (sizeof(TlvHeader) + length > capacity_ - length_) return false;
    TlvHeader header{type, length};
    memcpy(data_ + length_, &header, sizeof(header));
    length_ += sizeof(header);
    if (length) memcpy(data_ + length_, value, length);
    length_ += length;
    return true;
  }

  bool u16(uint16_t type, uint16_t value) { return append(type, &value, 2); }
  bool u32(uint16_t type, uint32_t value) { return append(type, &value, 4); }
  bool u64(uint16_t type, uint64_t value) { return append(type, &value, 8); }
  size_t length() const { return length_; }

 private:
  uint8_t* data_;
  size_t capacity_;
  size_t length_;
};

}  // namespace bridge
