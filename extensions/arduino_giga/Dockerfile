ARG CORE_IMAGE=jlink-mcp:local
FROM ${CORE_IMAGE}

ARG ARDUINO_CLI_VERSION=1.5.1
ARG ARDUINO_CLI_SHA256=28a8e119c498a25607821c36cb2dc49e8463941b261a0d99091baa7bc692dd2b
ARG ARDUINO_USB_HOST_MBED5_VERSION=0.3.1
ARG ARDUINO_BLE_VERSION=2.1.0
ARG ARDUINO_SPI_NINA_VERSION=0.0.2

USER root

ENV ARDUINO_DIRECTORIES_DATA=/opt/arduino/data \
    ARDUINO_DIRECTORIES_DOWNLOADS=/opt/arduino/downloads \
    ARDUINO_DIRECTORIES_USER=/opt/arduino/user \
    PATH=/opt/jlink-mcp/.venv/bin:/opt/arduino/data/packages/arduino/tools/arm-none-eabi-gcc/7-2017q4/bin:/opt/arduino/data/packages/arduino/tools/openocd/0.11.0-arduino2/bin:/usr/local/bin:/usr/bin:/bin

RUN curl -fsSL \
      "https://downloads.arduino.cc/arduino-cli/arduino-cli_${ARDUINO_CLI_VERSION}_Linux_64bit.tar.gz" \
      -o /tmp/arduino-cli.tar.gz \
    && echo "${ARDUINO_CLI_SHA256}  /tmp/arduino-cli.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/arduino-cli.tar.gz -C /usr/local/bin arduino-cli \
    && rm /tmp/arduino-cli.tar.gz \
    && arduino-cli version

COPY extensions/arduino_giga/container/arduino-cli.yaml /etc/arduino-cli.yaml
RUN mkdir -p /opt/arduino/data /opt/arduino/downloads /opt/arduino/user \
    && arduino-cli core update-index --config-file /etc/arduino-cli.yaml \
    && arduino-cli core install arduino:mbed_giga@4.6.0 \
         --config-file /etc/arduino-cli.yaml \
    && arduino-cli lib install \
         "Arduino_USBHostMbed5@${ARDUINO_USB_HOST_MBED5_VERSION}" \
         --config-file /etc/arduino-cli.yaml \
    && arduino-cli lib install \
         "Arduino_SpiNINA@${ARDUINO_SPI_NINA_VERSION}" \
         --config-file /etc/arduino-cli.yaml \
    && arduino-cli lib install \
         "ArduinoBLE@${ARDUINO_BLE_VERSION}" \
         --config-file /etc/arduino-cli.yaml \
    && arduino-cli lib list --config-file /etc/arduino-cli.yaml \
    && arduino-cli core list --config-file /etc/arduino-cli.yaml

COPY extensions/arduino_giga /tmp/arduino_giga
COPY extensions/giga_protocol_bridge /tmp/giga_protocol_bridge
RUN /opt/jlink-mcp/.venv/bin/python -m ensurepip \
    && /opt/jlink-mcp/.venv/bin/python -m pip install --no-cache-dir --no-deps \
         /tmp/arduino_giga /tmp/giga_protocol_bridge \
    && /opt/jlink-mcp/.venv/bin/python -m compileall -q \
         /opt/jlink-mcp/.venv/lib/python3.12/site-packages/jlink_mcp_arduino_giga \
         /opt/jlink-mcp/.venv/lib/python3.12/site-packages/jlink_mcp_giga_protocol_bridge \
    && rm -rf /tmp/arduino_giga /tmp/giga_protocol_bridge \
    && chown -R 1000:1000 /opt/arduino

ENV JLINK_MCP_EXTENSIONS=arduino_giga,giga_protocol_bridge \
    JLINK_MCP_GDB_CLIENT=/opt/arduino/data/packages/arduino/tools/arm-none-eabi-gcc/7-2017q4/bin/arm-none-eabi-gdb \
    JLINK_MCP_EXT_ARDUINO_GIGA__ARDUINO_CLI=/usr/local/bin/arduino-cli \
    JLINK_MCP_EXT_ARDUINO_GIGA__DATA_ROOT=/opt/arduino/data \
    JLINK_MCP_EXT_ARDUINO_GIGA__USER_ROOT=/opt/arduino/user

USER jlink
