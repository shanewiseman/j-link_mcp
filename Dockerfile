# syntax=docker/dockerfile:1.7

FROM python:3.12-slim-bookworm

ARG ARDUINO_CLI_VERSION=1.5.1
ARG ARDUINO_CLI_SHA256=28a8e119c498a25607821c36cb2dc49e8463941b261a0d99091baa7bc692dd2b
ARG UV_VERSION=0.8.15
ARG APP_UID=1000
ARG APP_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    ARDUINO_DIRECTORIES_DATA=/opt/arduino/data \
    ARDUINO_DIRECTORIES_DOWNLOADS=/opt/arduino/downloads \
    ARDUINO_DIRECTORIES_USER=/opt/arduino/user \
    PATH=/opt/jlink-mcp/.venv/bin:/opt/arduino/data/packages/arduino/tools/arm-none-eabi-gcc/7-2017q4/bin:/opt/arduino/data/packages/arduino/tools/openocd/0.11.0-arduino2/bin:/usr/local/bin:/usr/bin:/bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        at-spi2-core \
        ca-certificates \
        curl \
        dbus-x11 \
        file \
        git \
        imagemagick \
        libatomic1 \
        libfontconfig1 \
        libfreetype6 \
        libgl1 \
        libglib2.0-0 \
        libgtk2.0-0 \
        libice6 \
        libsm6 \
        libusb-1.0-0 \
        libx11-6 \
        libxext6 \
        libxrender1 \
        novnc \
        openbox \
        passwd \
        procps \
        python3-pyatspi \
        scrot \
        tesseract-ocr \
        tini \
        udev \
        websockify \
        x11vnc \
        xdotool \
        xvfb \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL \
      "https://downloads.arduino.cc/arduino-cli/arduino-cli_${ARDUINO_CLI_VERSION}_Linux_64bit.tar.gz" \
      -o /tmp/arduino-cli.tar.gz \
    && echo "${ARDUINO_CLI_SHA256}  /tmp/arduino-cli.tar.gz" | sha256sum -c - \
    && tar -xzf /tmp/arduino-cli.tar.gz -C /usr/local/bin arduino-cli \
    && rm /tmp/arduino-cli.tar.gz \
    && arduino-cli version

COPY container/arduino-cli.yaml /etc/arduino-cli.yaml
RUN mkdir -p /opt/arduino/data /opt/arduino/downloads /opt/arduino/user \
    && arduino-cli core update-index --config-file /etc/arduino-cli.yaml \
    && arduino-cli core install arduino:mbed_giga@4.6.0 \
         --config-file /etc/arduino-cli.yaml \
    && arduino-cli core list --config-file /etc/arduino-cli.yaml

WORKDIR /opt/jlink-mcp
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}" \
    && uv sync --frozen --no-dev --extra gui \
    && .venv/bin/python -m compileall -q src

RUN /usr/sbin/groupadd --gid "${APP_GID}" jlink \
    && /usr/sbin/useradd --uid "${APP_UID}" --gid "${APP_GID}" \
         --create-home --home-dir /home/jlink --shell /usr/sbin/nologin jlink \
    && mkdir -p /workspace /state /segger-state /home/jlink/.cache \
    && chown -R "${APP_UID}:${APP_GID}" \
         /workspace /state /segger-state /home/jlink

COPY --chmod=0755 container/entrypoint.sh /usr/local/bin/jlink-mcp-entrypoint

ENV HOME=/segger-state \
    JLINK_MCP_REPOSITORY_ROOT=/workspace \
    JLINK_MCP_WORKSPACE_ROOT=/workspace \
    JLINK_MCP_STATE_ROOT=/state \
    JLINK_MCP_SEGGER_ROOT=/opt/segger/JLink \
    JLINK_MCP_HOST_DEV_ROOT=/host/dev \
    JLINK_MCP_SYS_USB_ROOT=/sys/bus/usb/devices \
    JLINK_MCP_ARDUINO_CLI=/usr/local/bin/arduino-cli \
    JLINK_MCP_ARM_GDB=/opt/arduino/data/packages/arduino/tools/arm-none-eabi-gcc/7-2017q4/bin/arm-none-eabi-gdb \
    JLINK_MCP_TOKEN_FILE=/run/secrets/jlink_mcp_token \
    JLINK_MCP_HOST=127.0.0.1 \
    JLINK_MCP_PORT=8000 \
    DISPLAY=:99

USER jlink
ENTRYPOINT ["/bin/sh", "/usr/local/bin/jlink-mcp-entrypoint"]
CMD ["jlink-mcp", "serve"]
