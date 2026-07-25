#include <Arduino.h>
#include <RPC.h>

#include "JLinkMCPFixture.h"

extern "C" {
volatile uint32_t jlink_mcp_heartbeat = 0;
volatile uint32_t jlink_mcp_watch_value = 0x12345678u;
volatile uint32_t jlink_mcp_test_buffer[64] = {0};
volatile uint32_t jlink_mcp_last_command = 0;
volatile uint32_t jlink_mcp_uptime_ms = 0;
volatile uint32_t jlink_mcp_handshake = 0;
extern const uint32_t jlink_mcp_flash_constant
    __attribute__((used, externally_visible)) = 0xC0DEF17Eu;

__attribute__((noinline)) void jlink_mcp_breakpoint_site(void) {
  jlink_mcp_watch_value += 1;
  __asm volatile("nop");
}

__attribute__((noinline)) void jlink_mcp_fault_site(void) {
  volatile uint32_t *invalid = reinterpret_cast<volatile uint32_t *>(0xFFFFFFF0u);
  *invalid = 0xBAD00BADu;
}

__attribute__((noinline)) void jlink_mcp_deadlock_site(void) {
  while (true) {
    __asm volatile("nop");
  }
}

__attribute__((noinline)) void jlink_mcp_step_site(void) {
  jlink_mcp_watch_value ^= 0x01010101u;
  __asm volatile("nop\nnop\nnop");
}

__attribute__((noinline)) uint32_t jlink_mcp_stack_site(uint32_t depth) {
  volatile uint32_t frame_marker = 0x5A000000u | depth;
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

__attribute__((noinline)) void jlink_mcp_fault_level3(void) {
  jlink_mcp_fault_site();
}

__attribute__((noinline)) void jlink_mcp_fault_level2(void) {
  jlink_mcp_fault_level3();
}

__attribute__((noinline)) void jlink_mcp_fault_level1(void) {
  jlink_mcp_fault_level2();
}
}

static String input_line;
static uint32_t last_heartbeat_ms = 0;

// The build workflow patches several manifest fields after linking.  Access the
// flash object through volatile lvalues so LTO cannot substitute the link-time
// placeholder values into the validation protocol.
static const volatile JLinkMCPManifest &runtime_manifest() {
  return *reinterpret_cast<const volatile JLinkMCPManifest *>(
      &jlink_mcp_manifest);
}

static String manifest_text(const volatile char *source, size_t capacity) {
  String value;
  for (size_t index = 0; index < capacity; ++index) {
    const char byte = source[index];
    if (byte == '\0') {
      break;
    }
    value += byte;
  }
  return value;
}

uint32_t m7_status() { return jlink_mcp_heartbeat; }
uint32_t m7_watch_read() { return jlink_mcp_watch_value; }

static void emit(const String &line) {
  Serial.println(line);
  String framed = line + "\n";
  jlink_mcp_rtt_write(framed.c_str());
}

static void print_info() {
  emit(String("{\"event\":\"info\",\"fixture\":\"JLINK_MCP_HIL\","
              "\"protocol\":1,\"core\":\"m7\",\"heartbeat\":") +
       jlink_mcp_heartbeat + ",\"uptime_ms\":" + jlink_mcp_uptime_ms +
       ",\"watch\":" + jlink_mcp_watch_value +
       ",\"crc\":" +
       jlink_mcp_crc32(jlink_mcp_test_buffer, 64) + "}");
}

static void print_manifest() {
  const volatile JLinkMCPManifest &manifest = runtime_manifest();
  emit(String("{\"event\":\"manifest\",\"magic\":\"") +
       manifest_text(manifest.magic, sizeof(manifest.magic)) +
       "\",\"firmware_version\":" + manifest.firmware_version +
       ",\"protocol\":" + manifest.protocol_version + ",\"core_id\":" +
       manifest.core_id + ",\"git_commit\":\"" +
       manifest_text(manifest.git_commit, sizeof(manifest.git_commit)) +
       "\",\"build_id\":\"" +
       manifest_text(manifest.build_id, sizeof(manifest.build_id)) +
       "\",\"build_timestamp\":\"" +
       manifest_text(manifest.build_timestamp,
                     sizeof(manifest.build_timestamp)) +
       "\",\"image_size\":" + manifest.image_size +
       ",\"image_crc32\":" + manifest.image_crc32 +
       ",\"flash_start\":" + manifest.flash_start + ",\"flash_size\":" +
       manifest.flash_size + ",\"ram_start\":" + manifest.ram_start +
       ",\"ram_size\":" + manifest.ram_size + "}");
}

static void handle_command(String command) {
  command.trim();
  command.toUpperCase();
  ++jlink_mcp_last_command;
  if (command == "PING") {
    emit("{\"event\":\"pong\",\"core\":\"m7\"}");
  } else if (command == "INFO") {
    print_info();
  } else if (command == "BREAK") {
    jlink_mcp_breakpoint_site();
    emit("{\"event\":\"breakpoint-return\",\"core\":\"m7\"}");
  } else if (command == "FAULT") {
    emit("{\"event\":\"fault-enter\",\"core\":\"m7\"}");
    delay(10);
    jlink_mcp_fault_level1();
  } else if (command == "DEADLOCK") {
    emit("{\"event\":\"deadlock-enter\",\"core\":\"m7\"}");
    delay(10);
    jlink_mcp_deadlock_site();
  } else if (command == "RESET") {
    emit("{\"event\":\"reset-enter\",\"core\":\"m7\"}");
    delay(10);
    NVIC_SystemReset();
  } else if (command == "SWO") {
    jlink_mcp_swo_write("JLINK_MCP_SWO M7\n");
    emit("{\"event\":\"swo-emitted\",\"core\":\"m7\"}");
  } else if (command.startsWith("SET ")) {
    jlink_mcp_watch_value = strtoul(command.substring(4).c_str(), nullptr, 0);
    emit(String("{\"event\":\"watch-set\",\"value\":") +
         jlink_mcp_watch_value + "}");
  } else if (command == "GET") {
    print_info();
  } else if (command == "MANIFEST") {
    print_manifest();
  } else if (command == "STEP") {
    jlink_mcp_step_site();
    emit("{\"event\":\"step-return\",\"core\":\"m7\"}");
  } else if (command == "STACK") {
    const uint32_t value = jlink_mcp_stack_site(4);
    emit(String("{\"event\":\"stack-return\",\"value\":") + value + "}");
  } else if (command == "ASSERT") {
    emit("{\"event\":\"assert-enter\",\"core\":\"m7\"}");
    delay(10);
    jlink_mcp_assert_site();
  } else if (command == "WATCHDOG") {
    emit("{\"event\":\"watchdog-enter\",\"core\":\"m7\"}");
    delay(10);
    jlink_mcp_watchdog_site();
  } else if (command == "RPC") {
    auto value = RPC.call("jlink_mcp_m4_status").as<uint32_t>();
    jlink_mcp_handshake = value;
    emit(String("{\"event\":\"rpc\",\"m4_heartbeat\":") + value + "}");
  } else if (command == "SELFTEST") {
    const volatile uint32_t *flash_constant = &jlink_mcp_flash_constant;
    bool ok = *flash_constant == 0xC0DEF17Eu;
    for (uint32_t index = 0; index < 64; ++index) {
      jlink_mcp_test_buffer[index] = 0xA5000000u | index;
      ok = ok && jlink_mcp_test_buffer[index] == (0xA5000000u | index);
    }
    emit(String("{\"event\":\"selftest\",\"ok\":") +
         (ok ? "true" : "false") + ",\"crc\":" +
         jlink_mcp_crc32(jlink_mcp_test_buffer, 64) + "}");
  } else {
    emit(String("{\"event\":\"error\",\"reason\":\"unknown-command\","
                "\"command\":\"") +
         command + "\"}");
  }
}

void setup() {
  Serial.begin(115200);
  RPC.begin();
  RPC.bind("jlink_mcp_m7_status", m7_status);
  RPC.bind("jlink_mcp_m7_watch", m7_watch_read);
  pinMode(LED_BUILTIN, OUTPUT);
  for (uint32_t index = 0; index < 64; ++index) {
    jlink_mcp_test_buffer[index] = 0xA5000000u | index;
  }
  delay(150);
  emit("{\"event\":\"boot\",\"fixture\":\"JLINK_MCP_HIL\",\"core\":\"m7\","
       "\"protocol\":1}");
}

void loop() {
  while (Serial.available()) {
    char incoming = static_cast<char>(Serial.read());
    if (incoming == '\r' || incoming == '\n') {
      if (input_line.length()) {
        handle_command(input_line);
        input_line = "";
      }
    } else if (input_line.length() < 128) {
      input_line += incoming;
    }
  }

  const uint32_t now = millis();
  jlink_mcp_uptime_ms = now;
  if (now - last_heartbeat_ms >= 250) {
    last_heartbeat_ms = now;
    ++jlink_mcp_heartbeat;
    digitalWrite(LED_BUILTIN, (jlink_mcp_heartbeat & 1u) ? HIGH : LOW);
    if ((jlink_mcp_heartbeat & 0x0Fu) == 0) {
      emit(String("{\"event\":\"heartbeat\",\"core\":\"m7\",\"count\":") +
           jlink_mcp_heartbeat + "}");
    }
  }
  // Keep M7 continuously attachable. Mbed delay() may enter WFI, which can
  // force the STM32H747 J-Link target script to connect under reset and break
  // the dual-core OpenAMP startup handshake during automated validation.
  __asm volatile("nop");
}
