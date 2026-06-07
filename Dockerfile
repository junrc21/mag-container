ARG BASE_IMAGE=nousresearch/hermes-agent:main
FROM ${BASE_IMAGE}

# We keep runtime as user "hermes" (no root at runtime).
# Put bootstrap assets in /opt/hermes/bootstrap (image filesystem).
USER root

RUN mkdir -p /opt/hermes/bootstrap && chown -R hermes:hermes /opt/hermes/bootstrap

COPY --chown=hermes:hermes bootstrap/config.yaml /opt/hermes/bootstrap/config.yaml
COPY --chown=hermes:hermes bootstrap/soul.md /opt/hermes/bootstrap/soul.md
COPY --chown=hermes:hermes bootstrap/patch_byterover_plugin.py /opt/hermes/bootstrap/patch_byterover_plugin.py
COPY --chown=hermes:hermes bootstrap/patch_gateway_output.py /opt/hermes/bootstrap/patch_gateway_output.py
COPY --chown=hermes:hermes bootstrap/patch_approval_async.py /opt/hermes/bootstrap/patch_approval_async.py
COPY --chown=hermes:hermes bootstrap/patch_disable_channel_commands.py /opt/hermes/bootstrap/patch_disable_channel_commands.py
COPY --chown=hermes:hermes bootstrap/patch_usage_tokens.py /opt/hermes/bootstrap/patch_usage_tokens.py
COPY --chown=hermes:hermes bootstrap/patch_toolsets_used.py /opt/hermes/bootstrap/patch_toolsets_used.py
COPY --chown=hermes:hermes bootstrap/patch_credit_hardcap.py /opt/hermes/bootstrap/patch_credit_hardcap.py
COPY --chown=hermes:hermes entrypoint.sh /opt/hermes/entrypoint.sh

# MAG Google Workspace MCP server (stdio, zero-dependency Node). The MAG control
# plane wires it per-tenant via mcp_servers.google in the generated config.yaml.
RUN mkdir -p /opt/mag/google-mcp /opt/mag/linear-mcp /opt/mag/clickup-mcp && chown -R hermes:hermes /opt/mag
COPY --chown=hermes:hermes mcp/google/server.mjs /opt/mag/google-mcp/server.mjs

# MAG Linear + ClickUp MCP servers (stdio, zero-dependency Node). Use the connector
# token the user authorized in Fontes (fetched per-call from the MAG control plane).
COPY --chown=hermes:hermes mcp/linear/server.mjs /opt/mag/linear-mcp/server.mjs
COPY --chown=hermes:hermes mcp/clickup/server.mjs /opt/mag/clickup-mcp/server.mjs

# MAG Document Reader MCP (stdio, Node + pdf/docx/xlsx libs). Extracts text from
# uploaded documents so the agent can absorb it into ByteRover. Deps are installed
# at build time (no network needed at runtime).
RUN mkdir -p /opt/mag/docreader
COPY mcp/docreader/package.json /opt/mag/docreader/package.json
RUN cd /opt/mag/docreader && npm install --omit=dev --no-audit --no-fund && chown -R hermes:hermes /opt/mag/docreader
COPY --chown=hermes:hermes mcp/docreader/server.mjs /opt/mag/docreader/server.mjs

RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_byterover_plugin.py

# Anti-noise: extend the gateway's Telegram-only status/error sanitization to
# every end-user channel + humanize provider-error copy (see the script header).
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_gateway_output.py

# Async support-approval routing: dangerous commands are queued to the MAG admin
# panel instead of prompting the user (approvals.mode: async). See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_approval_async.py

# Disable gateway slash commands on client channels (Telegram/WhatsApp/etc.) so end
# users can't change the LLM model, restart/reset/yolo, etc. — only /start survives,
# everything else becomes normal text. Also empties the Telegram "/" menu. See header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_disable_channel_commands.py

# Usage metering: include per-turn token usage (tokens/cost/model) in the agent:end
# hook so the control plane can record real LLM cost per turn. See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_usage_tokens.py

# Per-tool credits: report the toolsets a turn used in agent:end, so the control
# plane can bill credits weighted by tool complexity. See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_toolsets_used.py

# Credit hard cap (Fase 2): block client-channel turns before the agent runs when
# the tenant is out of credits, with a humane message. See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_credit_hardcap.py

# Web search backend: ddgs (DuckDuckGo) — keyless, headless (no Chrome). The
# config pins web.backend=ddgs so the agent gets REAL results instead of trying
# the browser tool (no Chrome in this image) or an unconfigured paid provider.
RUN VIRTUAL_ENV=/opt/hermes/.venv uv pip install --python /opt/hermes/.venv/bin/python3 ddgs

# Free web EXTRACT backend: ddgs can only SEARCH; trafilatura extracts clean page
# content with NO API key and NO browser. Bundled as a web plugin (auto-discovered)
# and pinned via web.extract_backend=trafilatura in the generated config.
COPY --chown=hermes:hermes plugins/web/trafilatura /opt/hermes/plugins/web/trafilatura
COPY --chown=hermes:hermes bootstrap/patch_web_extract_free.py /opt/hermes/bootstrap/patch_web_extract_free.py
RUN VIRTUAL_ENV=/opt/hermes/.venv uv pip install --python /opt/hermes/.venv/bin/python3 trafilatura
# Make the keyless trafilatura extract backend selectable (web_tools hardcodes the
# availability allow-list and doesn't know it otherwise). See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_web_extract_free.py

# WhatsApp (Baileys) bridge: stop the silent drops. On loggedOut it re-arms pairing
# instead of process.exit(1); reconnects with capped backoff; and exposes /qr + /status
# so the control plane can drive QR pairing from the web. See script header.
COPY --chown=hermes:hermes bootstrap/patch_whatsapp_bridge.py /opt/hermes/bootstrap/patch_whatsapp_bridge.py
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_whatsapp_bridge.py

RUN chmod +x /opt/hermes/entrypoint.sh

USER hermes
ENV HOME=/opt/data
ENV XDG_DATA_HOME=/opt/data/.local/share
ENV XDG_CONFIG_HOME=/opt/data/.config
ENV BRV_INSTALL_DIR=/opt/data/.local/share/brv-cli
ENV PATH=/opt/data/.local/share/brv-cli/bin:/opt/hermes/bin:/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

ENTRYPOINT ["/opt/hermes/entrypoint.sh"]
