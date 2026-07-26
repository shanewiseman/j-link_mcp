FROM python:3.12-slim-bookworm AS base

ARG APP_UID=1000
ARG APP_GID=1000

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/jlink-mcp/.venv/bin:/usr/local/bin:/usr/bin:/bin

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        at-spi2-core \
        ca-certificates \
        curl \
        dbus-x11 \
        file \
        gdb-multiarch \
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

FROM base AS builder

ARG UV_VERSION=0.8.15
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/jlink-mcp/.venv
WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir "uv==${UV_VERSION}" \
    && uv sync --frozen --no-dev --extra gui --package jlink-mcp --no-editable \
    && /opt/jlink-mcp/.venv/bin/python -m compileall -q \
         /opt/jlink-mcp/.venv/lib/python3.12/site-packages/jlink_mcp

FROM base AS runtime

ARG APP_UID=1000
ARG APP_GID=1000

WORKDIR /opt/jlink-mcp
COPY --from=builder /opt/jlink-mcp/.venv /opt/jlink-mcp/.venv
COPY LICENSE ./LICENSE
COPY --chmod=0755 container/entrypoint.sh /usr/local/bin/jlink-mcp-entrypoint

RUN /usr/sbin/groupadd --gid "${APP_GID}" jlink \
    && /usr/sbin/useradd --uid "${APP_UID}" --gid "${APP_GID}" \
         --create-home --home-dir /home/jlink --shell /usr/sbin/nologin jlink \
    && mkdir -p /workspace /state /segger-state /home/jlink/.cache \
    && chown -R "${APP_UID}:${APP_GID}" \
         /workspace /state /segger-state /home/jlink

ENV HOME=/segger-state \
    JLINK_MCP_REPOSITORY_ROOT=/workspace \
    JLINK_MCP_WORKSPACE_ROOT=/workspace \
    JLINK_MCP_STATE_ROOT=/state \
    JLINK_MCP_SEGGER_ROOT=/opt/segger/JLink \
    JLINK_MCP_HOST_DEV_ROOT=/host/dev \
    JLINK_MCP_SYS_USB_ROOT=/sys/bus/usb/devices \
    JLINK_MCP_GDB_CLIENT=/usr/bin/gdb-multiarch \
    JLINK_MCP_EXTENSIONS= \
    JLINK_MCP_HOST=127.0.0.1 \
    JLINK_MCP_PORT=8000 \
    DISPLAY=:99

USER jlink
ENTRYPOINT ["/bin/sh", "/usr/local/bin/jlink-mcp-entrypoint"]
CMD ["jlink-mcp", "serve"]
