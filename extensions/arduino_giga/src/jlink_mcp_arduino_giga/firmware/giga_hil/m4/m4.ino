#include <Arduino.h>
#include <RPC.h>
#include <SerialRPC.h>

#include "JLinkMCPFixture.h"

extern "C" {
volatile uint32_t jlink_mcp_heartbeat = 0;
volatile uint32_t jlink_mcp_watch_value = 0x4D340001u;
volatile uint32_t jlink_mcp_test_buffer[64] = {0};
volatile uint32_t jlink_mcp_uptime_ms = 0;
extern const uint32_t jlink_mcp_flash_constant
    __attribute__((used, externally_visible)) = 0xC0DE0404u;

__attribute__((noinline)) void jlink_mcp_breakpoint_site(void) {
  jlink_mcp_watch_value += 1;
  __asm volatile("nop");
}

__attribute__((noinline)) void jlink_mcp_fault_site(void) {
  volatile uint32_t *invalid = reinterpret_cast<volatile uint32_t *>(0xFFFFFFF0u);
  *invalid = 0xBAD00404u;
}

__attribute__((noinline)) void jlink_mcp_deadlock_site(void) {
  while (true) {
    __asm volatile("nop");
  }
}

__attribute__((noinline)) void jlink_mcp_step_site(void) {
  jlink_mcp_watch_value ^= 0x04040404u;
  __asm volatile("nop\nnop\nnop");
}

__attribute__((noinline)) uint32_t jlink_mcp_stack_site(uint32_t depth) {
  volatile uint32_t frame_marker = 0x4D000000u | depth;
  if (depth == 0) {
    return frame_marker ^ jlink_mcp_watch_value;
  }
  return frame_marker ^ jlink_mcp_stack_site(depth - 1);
}

__attribute__((noinline)) void jlink_mcp_assert_site(void) {
  __builtin_trap();
}

__attribute__((noinline)) void jlink_mcp_watchdog_site(void) {
  NVIC_SystemReset();
}
}

uint32_t m4_status() { return jlink_mcp_heartbeat; }
uint32_t m4_watch_read() { return jlink_mcp_watch_value; }
uint32_t m4_add(uint32_t left, uint32_t right) { return left + right; }

void setup() {
  SerialRPC.begin();
  RPC.bind("jlink_mcp_m4_status", m4_status);
  RPC.bind("jlink_mcp_m4_watch", m4_watch_read);
  RPC.bind("jlink_mcp_m4_add", m4_add);
  for (uint32_t index = 0; index < 64; ++index) {
    jlink_mcp_test_buffer[index] = 0x4D340000u | index;
  }
  jlink_mcp_rtt_write(
      "{\"event\":\"boot\",\"fixture\":\"JLINK_MCP_HIL\","
      "\"core\":\"m4\",\"protocol\":1}\n");
  jlink_mcp_rtt_write(jlink_mcp_manifest.magic);
  SerialRPC.println(
      "{\"event\":\"boot\",\"fixture\":\"JLINK_MCP_HIL\","
      "\"core\":\"m4\",\"protocol\":1}");
}

void loop() {
  jlink_mcp_uptime_ms = millis();
  ++jlink_mcp_heartbeat;
  if ((jlink_mcp_heartbeat & 0x07u) == 0) {
    jlink_mcp_breakpoint_site();
  }
  if ((jlink_mcp_heartbeat & 0x1Fu) == 0) {
    String line =
        String("{\"event\":\"heartbeat\",\"core\":\"m4\",\"count\":") +
        jlink_mcp_heartbeat + "}";
    SerialRPC.println(line);
    jlink_mcp_rtt_write((line + "\n").c_str());
  }
  // Keep the secondary core clocked while validation is active. The Mbed
  // implementation of delay() may enter WFI, and the STM32H747 M4 J-Link
  // target script cannot attach while M7 is holding a sleeping M4 through an
  // external reset. A bounded busy wait preserves deterministic timing and
  // continuous debug accessibility for the HIL fixture.
  const uint32_t wait_started = millis();
  while (static_cast<uint32_t>(millis() - wait_started) < 100u) {
    __asm volatile("nop");
  }
}
