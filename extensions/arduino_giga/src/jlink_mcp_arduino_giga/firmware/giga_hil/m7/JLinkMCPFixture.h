#pragma once

#include <Arduino.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "JLinkMCPBuildIdentity.h"

#ifndef JLINK_MCP_GIT_COMMIT
#define JLINK_MCP_GIT_COMMIT "unknown"
#endif

#ifndef JLINK_MCP_BUILD_ID
#define JLINK_MCP_BUILD_ID __DATE__ "T" __TIME__
#endif

#ifndef JLINK_MCP_BUILD_TIMESTAMP
#define JLINK_MCP_BUILD_TIMESTAMP __DATE__ "T" __TIME__
#endif

extern "C" {
extern volatile uint32_t jlink_mcp_heartbeat;
extern volatile uint32_t jlink_mcp_watch_value;
extern volatile uint32_t jlink_mcp_test_buffer[64];
extern const uint32_t jlink_mcp_flash_constant;
void jlink_mcp_breakpoint_site(void);
void jlink_mcp_fault_site(void);
void jlink_mcp_deadlock_site(void);
void jlink_mcp_step_site(void);
uint32_t jlink_mcp_stack_site(uint32_t depth);
void jlink_mcp_assert_site(void);
void jlink_mcp_watchdog_site(void);
}

struct JLinkMCPManifest {
  char magic[16];
  uint32_t firmware_version;
  uint32_t protocol_version;
  uint32_t core_id;
  char git_commit[41];
  char build_id[32];
  char build_timestamp[32];
  uint32_t image_size;
  uint32_t image_crc32;
  uint32_t flash_start;
  uint32_t flash_size;
  uint32_t ram_start;
  uint32_t ram_size;
  const void *heartbeat_address;
  const void *watch_address;
  const void *test_buffer_address;
  const void *breakpoint_address;
  const void *fault_address;
  const void *step_address;
  const void *stack_address;
  const void *assert_address;
  const void *watchdog_address;
  const void *flash_constant_address;
};

static_assert(offsetof(JLinkMCPManifest, image_size) == 136,
              "post-link manifest layout changed");
static_assert(offsetof(JLinkMCPManifest, ram_size) == 156,
              "post-link manifest layout changed");

extern "C" const JLinkMCPManifest jlink_mcp_manifest;
extern "C" __attribute__((used, section(".jlink_mcp_manifest")))
const JLinkMCPManifest jlink_mcp_manifest = {
    "JLINK_MCP_HIL",
    1,
    1,
    7,
    JLINK_MCP_GIT_COMMIT,
    JLINK_MCP_BUILD_ID,
    JLINK_MCP_BUILD_TIMESTAMP,
    0xFFFFFFFFu,
    0xFFFFFFFFu,
    0xFFFFFFFFu,
    0xFFFFFFFFu,
    0xFFFFFFFFu,
    0xFFFFFFFFu,
    (const void *)&jlink_mcp_heartbeat,
    (const void *)&jlink_mcp_watch_value,
    (const void *)&jlink_mcp_test_buffer,
    (const void *)&jlink_mcp_breakpoint_site,
    (const void *)&jlink_mcp_fault_site,
    (const void *)&jlink_mcp_step_site,
    (const void *)&jlink_mcp_stack_site,
    (const void *)&jlink_mcp_assert_site,
    (const void *)&jlink_mcp_watchdog_site,
    (const void *)&jlink_mcp_flash_constant,
};

struct RTTBuffer {
  const char *name;
  char *buffer;
  unsigned size;
  volatile unsigned write_offset;
  volatile unsigned read_offset;
  unsigned flags;
};

struct RTTControlBlock {
  char id[16];
  int max_up_buffers;
  int max_down_buffers;
  RTTBuffer up[1];
  RTTBuffer down[1];
};

static char jlink_mcp_rtt_up_buffer[1024];
static char jlink_mcp_rtt_down_buffer[64];

extern "C" __attribute__((used)) RTTControlBlock _SEGGER_RTT = {
    "SEGGER RTT",
    1,
    1,
    {{"Terminal", jlink_mcp_rtt_up_buffer, sizeof(jlink_mcp_rtt_up_buffer), 0, 0,
      0}},
    {{"Terminal", jlink_mcp_rtt_down_buffer, sizeof(jlink_mcp_rtt_down_buffer),
      0, 0, 0}},
};

inline void jlink_mcp_rtt_write(const char *text) {
  RTTBuffer &channel = _SEGGER_RTT.up[0];
  while (*text) {
    unsigned next = channel.write_offset + 1;
    if (next == channel.size) {
      next = 0;
    }
    if (next == channel.read_offset) {
      break;
    }
    channel.buffer[channel.write_offset] = *text++;
    channel.write_offset = next;
  }
}

inline void jlink_mcp_swo_write(const char *text) {
#if defined(ITM)
  while (*text) {
    ITM_SendChar(static_cast<uint32_t>(*text++));
  }
#else
  (void)text;
#endif
}

inline uint32_t jlink_mcp_crc32(const volatile uint32_t *data, size_t words) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t index = 0; index < words; ++index) {
    uint32_t value = data[index];
    for (unsigned byte = 0; byte < 4; ++byte) {
      crc ^= value & 0xFFu;
      value >>= 8;
      for (unsigned bit = 0; bit < 8; ++bit) {
        crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
      }
    }
  }
  return ~crc;
}
