#!/bin/sh
set -eu

if [ "${JLINK_MCP_ENABLE_NOVNC:-false}" = "true" ]; then
  Xvfb "${DISPLAY:-:99}" -screen 0 1280x1024x24 -nolisten tcp &
  openbox-session >/tmp/openbox.log 2>&1 &
  x11vnc -display "${DISPLAY:-:99}" -localhost -forever -shared -nopw \
    >/tmp/x11vnc.log 2>&1 &
  websockify --web=/usr/share/novnc/ \
    "${JLINK_MCP_NOVNC_LISTEN:-127.0.0.1:6080}" localhost:5900 \
    >/tmp/novnc.log 2>&1 &
fi

export NO_AT_BRIDGE=0
JLINK_MCP_TOKEN_FILE=${JLINK_MCP_TOKEN_FILE:-/run/secrets/jlink_mcp_token}
export JLINK_MCP_TOKEN_FILE
exec /usr/bin/tini -- dbus-run-session -- "$@"
