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
COPY --chown=hermes:hermes bootstrap/patch_no_review_delivery.py /opt/hermes/bootstrap/patch_no_review_delivery.py
COPY --chown=hermes:hermes bootstrap/patch_gateway_system_copy.py /opt/hermes/bootstrap/patch_gateway_system_copy.py
COPY --chown=hermes:hermes bootstrap/patch_approval_async.py /opt/hermes/bootstrap/patch_approval_async.py
COPY --chown=hermes:hermes bootstrap/patch_channel_noise_suppress.py /opt/hermes/bootstrap/patch_channel_noise_suppress.py
COPY --chown=hermes:hermes bootstrap/patch_disable_channel_commands.py /opt/hermes/bootstrap/patch_disable_channel_commands.py
COPY --chown=hermes:hermes bootstrap/patch_usage_tokens.py /opt/hermes/bootstrap/patch_usage_tokens.py
COPY --chown=hermes:hermes bootstrap/patch_toolsets_used.py /opt/hermes/bootstrap/patch_toolsets_used.py
COPY --chown=hermes:hermes bootstrap/patch_credit_hardcap.py /opt/hermes/bootstrap/patch_credit_hardcap.py
COPY --chown=hermes:hermes bootstrap/patch_cron_job_runs.py /opt/hermes/bootstrap/patch_cron_job_runs.py
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

# Never leak the background "self-improvement review" summary (e.g. "💾 Self-improvement
# review: User profile updated") to client channels — it bypasses the sanitizer via a
# direct status-adapter send. This wires that delivery callback to None. See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_no_review_delivery.py

# Humanize/suppress stock Hermes gateway SYSTEM messages (pairing prompt, home-channel nag)
# that bypass the persona + sanitizer and leak the stack name / CLI to the client. See header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_gateway_system_copy.py

# Async support-approval routing: dangerous commands are queued to the MAG admin
# panel instead of prompting the user (approvals.mode: async). See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_approval_async.py

# Kill 3 client-channel leaks found in MAG E2E (§17/§18): execute_code approval prompt,
# /busy "Interrupting current task" notice, and the /busy first-time tip. See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_channel_noise_suppress.py

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

# Cron run history: report EVERY cron run (success/failure/delivery error) to the
# control plane (POST /internal/runtime/<slug>/job-runs → mag_job_runs), so the
# client panel can show per-routine run history. Best-effort, never breaks cron.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_cron_job_runs.py

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

# Bake the WhatsApp bridge deps (Baileys) into the image. Otherwise the bridge runs a
# slow/fragile ~3-min `npm install` on the FIRST pairing at runtime — which looks like
# "the QR never generates". Baking it means the first QR is instant for every tenant.
RUN cd /opt/hermes/scripts/whatsapp-bridge && npm install --no-audit --no-fund \
    && chown -R hermes:hermes node_modules

# WhatsApp web pairing: a self-contained pairing module + 4 thin gateway routes
# (/api/whatsapp/{pair,qr,status,logout}) so the control plane can drive QR pairing.
# qrcode renders the QR string to a PNG data-URL server-side (Pillow already present).
COPY --chown=hermes:hermes bootstrap/mag_whatsapp_pairing.py /opt/hermes/gateway/platforms/mag_whatsapp_pairing.py
COPY --chown=hermes:hermes bootstrap/patch_whatsapp_gateway.py /opt/hermes/bootstrap/patch_whatsapp_gateway.py
RUN VIRTUAL_ENV=/opt/hermes/.venv uv pip install --python /opt/hermes/.venv/bin/python3 qrcode
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_whatsapp_gateway.py

# Telegram pairing approval over HTTP: lets the web approve the pairing code Hermes DMs
# an un-allowlisted user (delegates to Hermes' own PairingStore). 3 thin gateway routes
# (/api/telegram/pairing[/approve|/revoke]). Runs AFTER the WhatsApp gateway patch — it
# anchors on the WhatsApp routes that patch inserts. See script headers.
COPY --chown=hermes:hermes bootstrap/mag_telegram_pairing.py /opt/hermes/gateway/platforms/mag_telegram_pairing.py
COPY --chown=hermes:hermes bootstrap/patch_telegram_gateway.py /opt/hermes/bootstrap/patch_telegram_gateway.py
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_telegram_gateway.py

# Browser automation (Diretora tier): bake a system Chromium so agent-browser's
# local backend has a Chrome to drive. agent-browser auto-detects /usr/bin/chromium
# (verified: open + snapshot work). It lives in the image — NOT under /opt/data (the
# per-tenant volume) — so it's found at runtime regardless of HOME. The `browser`
# toolset is gated per plan (enabled only for enterprise/Diretora).
RUN apt-get update && apt-get install -y --no-install-recommends chromium \
    && rm -rf /var/lib/apt/lists/*

# Timezone: the whole platform runs on Brasília time. HERMES_TIMEZONE is read by
# hermes_time.now() (the clock behind cron schedules + delivery), TZ covers OS-level
# time. tzdata in the venv guarantees ZoneInfo("America/Sao_Paulo") resolves even if
# the base OS ships no zoneinfo. The per-tenant generated config/.env also set these
# (control plane), so a reload keeps them; this is the image-level default.
RUN VIRTUAL_ENV=/opt/hermes/.venv uv pip install --python /opt/hermes/.venv/bin/python3 tzdata

RUN chmod +x /opt/hermes/entrypoint.sh

USER hermes
ENV TZ=America/Sao_Paulo
ENV HERMES_TIMEZONE=America/Sao_Paulo
ENV HOME=/opt/data
ENV XDG_DATA_HOME=/opt/data/.local/share
ENV XDG_CONFIG_HOME=/opt/data/.config
ENV BRV_INSTALL_DIR=/opt/data/.local/share/brv-cli
ENV PATH=/opt/data/.local/share/brv-cli/bin:/opt/hermes/bin:/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

ENTRYPOINT ["/opt/hermes/entrypoint.sh"]
