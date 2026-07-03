# Hermes base PINADO no DIGEST que a última imagem de prod usou (build verde de 2026-06-18,
# run CI 27793975658). NÃO usar `:main` (tag móvel): ela já avançou e o refactor do cron
# scheduler upstream quebra patch_cron_job_runs/patch_sanitize_cron_errors; e tags antigas
# (v2026.6.5) são velhas demais p/ patch_whatsapp_boot_deps. Este digest é o único ponto
# onde TODOS os patches (equipe + MAG) aplicam — é exatamente o Hermes que o runtime de prod
# já roda (zero mudança de comportamento). Bump de Hermes = trocar o digest + revalidar patches.
ARG BASE_IMAGE=nousresearch/hermes-agent@sha256:20c40d8c948254e1167827289b09300a476bae2eddc23a9d4a24bfde4567408e
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
COPY --chown=hermes:hermes bootstrap/patch_forbidden_topics_gate.py /opt/hermes/bootstrap/patch_forbidden_topics_gate.py
COPY --chown=hermes:hermes bootstrap/patch_cron_job_runs.py /opt/hermes/bootstrap/patch_cron_job_runs.py
COPY --chown=hermes:hermes bootstrap/patch_disable_channel_code_exec.py /opt/hermes/bootstrap/patch_disable_channel_code_exec.py
COPY --chown=hermes:hermes bootstrap/patch_suppress_reset_banner.py /opt/hermes/bootstrap/patch_suppress_reset_banner.py
COPY --chown=hermes:hermes entrypoint.sh /opt/hermes/entrypoint.sh

# MAG Google Workspace + OneDrive MCP servers (stdio, zero-dependency Node). The
# MAG control plane wires them per-tenant via generated mcp_servers entries.
RUN mkdir -p /opt/mag/google-mcp /opt/mag/onedrive-mcp /opt/mag/linear-mcp /opt/mag/clickup-mcp && chown -R hermes:hermes /opt/mag
COPY --chown=hermes:hermes mcp/google/server.mjs /opt/mag/google-mcp/server.mjs
COPY --chown=hermes:hermes mcp/onedrive/server.mjs /opt/mag/onedrive-mcp/server.mjs

# MAG Linear + ClickUp MCP servers (stdio, zero-dependency Node). Use the connector
# token the user authorized in Fontes (fetched per-call from the MAG control plane).
COPY --chown=hermes:hermes mcp/linear/server.mjs /opt/mag/linear-mcp/server.mjs
COPY --chown=hermes:hermes mcp/clickup/server.mjs /opt/mag/clickup-mcp/server.mjs

# MAG Custom Proxy MCP server (stdio, zero-dependency Node). Reads CUSTOM_CONNECTOR_CONFIG
# env var (JSON with baseUrl + apiKey) and exposes a generic http_request tool for
# calling arbitrary HTTP APIs. Enables users to connect any REST API as a knowledge source.
RUN mkdir -p /opt/mag/custom-proxy-mcp && chown -R hermes:hermes /opt/mag
COPY --chown=hermes:hermes mcp/custom-proxy/server.mjs /opt/mag/custom-proxy-mcp/server.mjs

# ByteRover memory OAuth helper — driven by the control plane (admin "Conectar memória").
# Talks to the per-tenant brv daemon's transport (startOAuth/awaitOAuthCallback). See header.
COPY --chown=hermes:hermes mcp/brv/oauth-helper.mjs /opt/mag/brv-oauth-helper.mjs

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

# Restricted topics: block tenant-defined sensitive themes on client channels
# before the model runs, unless the sender is explicitly allowlisted for that
# exact rule (Telegram user ID / WhatsApp number). See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_forbidden_topics_gate.py

# Cron run history: report EVERY cron run (success/failure/delivery error) to the
# control plane (POST /internal/runtime/<slug>/job-runs → mag_job_runs), so the
# client panel can show per-routine run history. Best-effort, never breaks cron.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_cron_job_runs.py

# Task A: on client channels, remove the code_execution toolset entirely (not just
# deny at approval) so the model never loops calling execute_code -> deny -> retry
# (~60s+ stall before refusing). Internal surfaces keep it. See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_disable_channel_code_exec.py

# Suppress the auto-reset banner on client channels — it leaks the AI model/provider,
# config.yaml internals and slash commands in English. The agent still gets the
# internal context_note; the user just continues in a fresh session. See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_suppress_reset_banner.py

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

# WhatsApp bridge boot fix: don't force npm install on every boot when a usable
# node_modules tree already exists but predates the dependency stamp. Backfill
# the stamp automatically and tolerate npm failures when deps are already usable.
COPY --chown=hermes:hermes bootstrap/patch_whatsapp_boot_deps.py /opt/hermes/bootstrap/patch_whatsapp_boot_deps.py
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_whatsapp_boot_deps.py

# WhatsApp JID normalization: ensure groups use @g.us and DMs use @s.whatsapp.net suffix.
# Prevents jidDecode failures when users send messages to targets listed without suffix.
COPY --chown=hermes:hermes bootstrap/patch_whatsapp_jid_normalization.py /opt/hermes/bootstrap/patch_whatsapp_jid_normalization.py
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_whatsapp_jid_normalization.py

# WhatsApp outbound proativo: adiciona POST /send ao bridge com guard de allowlist.
# Deve rodar APÓS patch_whatsapp_jid_normalization (usa normalizeWhatsAppJid).
COPY --chown=hermes:hermes bootstrap/patch_whatsapp_outbound.py /opt/hermes/bootstrap/patch_whatsapp_outbound.py
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_whatsapp_outbound.py

# ACK-wait: faz o /send aguardar confirmação do servidor WA antes de retornar sucesso.
# Sem isso, erros como 463 (RESTRICT_ALL_COMPANIONS) chegam de forma assíncrona e o
# agente nunca sabe que a mensagem foi rejeitada. Deve rodar APÓS patch_whatsapp_outbound.
COPY --chown=hermes:hermes bootstrap/patch_whatsapp_ack_check.py /opt/hermes/bootstrap/patch_whatsapp_ack_check.py
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_whatsapp_ack_check.py

# MCP server whatsapp-outbound (stdio, zero-dependency Node). Expõe send_whatsapp_message
# ao agente para envio proativo de mensagens a contatos autorizados.
RUN mkdir -p /opt/mag/whatsapp-outbound-mcp && chown -R hermes:hermes /opt/mag
COPY --chown=hermes:hermes mcp/whatsapp-outbound/server.mjs /opt/mag/whatsapp-outbound-mcp/server.mjs

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

# Sanitize cron job error messages sent to client channels (Telegram/WhatsApp/etc.)
# When a cron job fails (e.g. "No Codex credentials stored"), Hermes sends the raw
# technical error directly to the channel, violating product secrecy. This patch
# intercepts those errors and replaces them with a generic user-friendly message.
# See script header for details.
COPY --chown=hermes:hermes bootstrap/patch_sanitize_cron_errors.py /opt/hermes/bootstrap/patch_sanitize_cron_errors.py
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_sanitize_cron_errors.py

# Telegram block list: make the deny path authoritative in Hermes core. A blocked user
# is denied even if present in TELEGRAM_ALLOWED_USERS, and is never re-issued a pairing
# code (so denied users don't reappear in the panel's pending list). Block-list storage
# lives in mag_telegram_pairing.py (copied above); this patch injects two checks that
# consult it (_is_user_authorized + the unauthorized-DM handler). See script header.
COPY --chown=hermes:hermes bootstrap/patch_authz_blocklist.py /opt/hermes/bootstrap/patch_authz_blocklist.py
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_authz_blocklist.py

# Browser automation (Diretora tier): bake a system Chromium so agent-browser's
# local backend has a Chrome to drive. agent-browser auto-detects /usr/bin/chromium
# (verified: open + snapshot work). It lives in the image — NOT under /opt/data (the
# per-tenant volume) — so it's found at runtime regardless of HOME. The `browser`
# toolset is gated per plan (enabled only for enterprise/Diretora).
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    tesseract-ocr \
    tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*

# PDF reading: pymupdf + pymupdf4llm for the ocr-and-documents skill.
# Without these, the agent gets ModuleNotFoundError when trying to read
# user-uploaded PDFs and falls back to claiming "needs selectable text".
# Chromium (above) covers PDF generation via --headless --print-to-pdf.
# pytesseract + tesseract-ocr (above) enable OCR on scanned/image-only PDFs
# via pymupdf's page.get_textpage_ocr() — returns empty string without it.
RUN VIRTUAL_ENV=/opt/hermes/.venv uv pip install \
    --python /opt/hermes/.venv/bin/python3 \
    pymupdf pymupdf4llm pytesseract

# MAG-bundled skills seeded into the tenant volume by entrypoint.sh.
# Skills live at runtime under /opt/data/skills/ (the tenant volume).
# Storing them here avoids losing them on image rebuild while keeping
# the seeding idempotent (entrypoint never overwrites existing skills).
RUN mkdir -p /opt/hermes/bootstrap/skills/productivity/pdf-generation
COPY --chown=hermes:hermes bootstrap/skills/productivity/pdf-generation/SKILL.md \
    /opt/hermes/bootstrap/skills/productivity/pdf-generation/SKILL.md

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

