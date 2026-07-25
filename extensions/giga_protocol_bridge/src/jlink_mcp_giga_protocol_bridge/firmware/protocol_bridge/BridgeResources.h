#pragma once

#include <Arduino.h>

namespace bridge {

enum Protocol : uint8_t {
  PROTO_SPI = 0,
  PROTO_I2C = 1,
  PROTO_UART = 2,
  PROTO_CAN = 3,
  PROTO_USB = 4,
  PROTO_WIFI = 5,
  PROTO_BLE = 6,
  PROTO_GPIO = 7,
};

inline bool safeDynamicPin(uint16_t pin) {
  return (pin >= 2 && pin <= 10) || (pin >= 22 && pin <= 85);
}

class PinResources {
 public:
  PinResources() { memset(owners_, 0, sizeof(owners_)); }

  bool claim(uint16_t pin, uint8_t owner) {
    if (!safeDynamicPin(pin) || owner == 0) return false;
    if (owners_[pin] != 0 && owners_[pin] != owner) return false;
    owners_[pin] = owner;
    return true;
  }

  bool claimPair(uint16_t first, uint16_t second, uint8_t owner) {
    if (!safeDynamicPin(first) || !safeDynamicPin(second) || owner == 0)
      return false;
    if ((owners_[first] != 0 && owners_[first] != owner) ||
        (owners_[second] != 0 && owners_[second] != owner)) return false;
    owners_[first] = owner;
    owners_[second] = owner;
    return true;
  }

  void releaseOwner(uint8_t owner) {
    for (size_t pin = 0; pin < sizeof(owners_); ++pin) {
      if (owners_[pin] == owner) owners_[pin] = 0;
    }
  }

  uint8_t owner(uint16_t pin) const { return pin < sizeof(owners_) ? owners_[pin] : 0xFF; }

 private:
  uint8_t owners_[103];
};

#pragma pack(push, 1)
struct QueueRecordHeader {
  uint8_t channel;
  uint16_t payload_length;
  uint64_t timestamp_us;
};
#pragma pack(pop)

class SharedReceiveQueue {
 public:
  static constexpr size_t kQueueCount = 6;
  static constexpr size_t kStorageSize = 64 * 1024;
  static constexpr size_t kSliceSize = kStorageSize / kQueueCount;

  SharedReceiveQueue() {
    memset(storage_, 0, sizeof(storage_));
    memset(queues_, 0, sizeof(queues_));
  }

  bool push(uint8_t protocol, uint8_t channel, const uint8_t* payload,
            uint16_t length, uint64_t timestamp_us) {
    Queue* queue = select(protocol);
    if (!queue) return false;
    const size_t record_size = sizeof(QueueRecordHeader) + length;
    if (record_size > kSliceSize - queue->used) {
      ++queue->overflow;
      return false;
    }
    QueueRecordHeader header{channel, length, timestamp_us};
    write(*queue, reinterpret_cast<const uint8_t*>(&header), sizeof(header));
    write(*queue, payload, length);
    return true;
  }

  size_t read(uint8_t protocol, uint8_t channel, uint8_t* output, size_t limit,
              bool drain) {
    Queue* queue = select(protocol);
    if (!queue || limit < sizeof(QueueRecordHeader)) return 0;
    size_t cursor = queue->tail;
    size_t remaining = queue->used;
    size_t retained = 0;
    size_t written = 0;
    while (remaining >= sizeof(QueueRecordHeader)) {
      QueueRecordHeader header{};
      peek(*queue, cursor, reinterpret_cast<uint8_t*>(&header), sizeof(header));
      const size_t record_size = sizeof(header) + header.payload_length;
      if (record_size > remaining) break;
      if (header.channel == channel) {
        if (record_size > limit - written) break;
        peek(*queue, cursor, output + written, record_size);
        written += record_size;
      } else if (drain) {
        copy(*queue, cursor, (queue->tail + retained) % kSliceSize,
             record_size);
        retained += record_size;
      }
      cursor = (cursor + record_size) % kSliceSize;
      remaining -= record_size;
    }
    if (drain) {
      copy(*queue, cursor, (queue->tail + retained) % kSliceSize, remaining);
      retained += remaining;
      queue->used = retained;
      queue->head = (queue->tail + retained) % kSliceSize;
    }
    return written;
  }

  uint32_t depth(uint8_t protocol) const {
    const Queue* queue = selectConst(protocol);
    return queue ? queue->used : 0;
  }

  uint32_t depth(uint8_t protocol, uint8_t channel) const {
    const Queue* queue = selectConst(protocol);
    if (!queue) return 0;
    size_t cursor = queue->tail;
    size_t remaining = queue->used;
    uint32_t matched = 0;
    while (remaining >= sizeof(QueueRecordHeader)) {
      QueueRecordHeader header{};
      peek(*queue, cursor, reinterpret_cast<uint8_t*>(&header), sizeof(header));
      const size_t record_size = sizeof(header) + header.payload_length;
      if (record_size > remaining) break;
      if (header.channel == channel) matched += record_size;
      cursor = (cursor + record_size) % kSliceSize;
      remaining -= record_size;
    }
    return matched;
  }

  uint32_t overflow(uint8_t protocol) const {
    const Queue* queue = selectConst(protocol);
    return queue ? queue->overflow : 0;
  }

 private:
  struct Queue {
    size_t head;
    size_t tail;
    size_t used;
    uint32_t overflow;
  };

  Queue* select(uint8_t protocol) {
    if (protocol < PROTO_UART || protocol > PROTO_GPIO) return nullptr;
    return &queues_[protocol - PROTO_UART];
  }

  const Queue* selectConst(uint8_t protocol) const {
    if (protocol < PROTO_UART || protocol > PROTO_GPIO) return nullptr;
    return &queues_[protocol - PROTO_UART];
  }

  size_t offset(const Queue& queue, size_t relative) const {
    const size_t index = static_cast<size_t>(&queue - queues_);
    return index * kSliceSize + relative;
  }

  void write(Queue& queue, const uint8_t* data, size_t length) {
    for (size_t index = 0; index < length; ++index) {
      storage_[offset(queue, queue.head)] = data[index];
      queue.head = (queue.head + 1) % kSliceSize;
    }
    queue.used += length;
  }

  void copy(Queue& queue, size_t source, size_t destination, size_t length) {
    for (size_t index = 0; index < length; ++index) {
      const uint8_t value = storage_[offset(queue, source)];
      storage_[offset(queue, destination)] = value;
      source = (source + 1) % kSliceSize;
      destination = (destination + 1) % kSliceSize;
    }
  }

  void peek(const Queue& queue, size_t cursor, uint8_t* output, size_t length) const {
    for (size_t index = 0; index < length; ++index) {
      output[index] = storage_[offset(queue, cursor)];
      cursor = (cursor + 1) % kSliceSize;
    }
  }

  uint8_t storage_[kStorageSize];
  Queue queues_[kQueueCount];
};

}  // namespace bridge
