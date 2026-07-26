__attribute__((section(".manifest"))) unsigned char jlink_mcp_manifest[200];
__attribute__((section(".ram"))) unsigned char jlink_mcp_test_buffer[32];
__attribute__((section(".rtt"))) unsigned char _SEGGER_RTT[64];

void jlink_mcp_breakpoint_site(void) {}
