# Hermes base PINADO no DIGEST que a última imagem de prod usou (build verde de 2026-06-18,
# run CI 27793975658). NÃO usar `:main` (tag móvel): ela já avançou e o refactor do cron
# scheduler upstream quebra patch_cron_job_runs/patch_sanitize_cron_errors. Este digest é o
# único ponto onde TODOS os patches (equipe + MAG) aplicam — é exatamente o Hermes que o
# runtime de prod já roda (zero mudança de comportamento). Bump de Hermes = trocar o digest
# + revalidar patches.
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
COPY --chown=hermes:hermes bootstrap/patch_aux_usage_ledger.py /opt/hermes/bootstrap/patch_aux_usage_ledger.py
COPY --chown=hermes:hermes bootstrap/mag_turn_ledger.py /opt/hermes/agent/mag_turn_ledger.py
COPY --chown=hermes:hermes bootstrap/patch_toolsets_used.py /opt/hermes/bootstrap/patch_toolsets_used.py
COPY --chown=hermes:hermes bootstrap/patch_enable_send_message.py /opt/hermes/bootstrap/patch_enable_send_message.py
COPY --chown=hermes:hermes bootstrap/patch_admin_block.py /opt/hermes/bootstrap/patch_admin_block.py
COPY --chown=hermes:hermes bootstrap/patch_credit_hardcap.py /opt/hermes/bootstrap/patch_credit_hardcap.py
COPY --chown=hermes:hermes bootstrap/patch_credit_warning.py /opt/hermes/bootstrap/patch_credit_warning.py
COPY --chown=hermes:hermes bootstrap/patch_forbidden_topics_gate.py /opt/hermes/bootstrap/patch_forbidden_topics_gate.py
COPY --chown=hermes:hermes bootstrap/patch_cron_job_runs.py /opt/hermes/bootstrap/patch_cron_job_runs.py
COPY --chown=hermes:hermes bootstrap/patch_disable_channel_code_exec.py /opt/hermes/bootstrap/patch_disable_channel_code_exec.py
COPY --chown=hermes:hermes bootstrap/patch_suppress_reset_banner.py /opt/hermes/bootstrap/patch_suppress_reset_banner.py
COPY --chown=hermes:hermes bootstrap/patch_suppress_agent_diagnostics.py /opt/hermes/bootstrap/patch_suppress_agent_diagnostics.py
COPY --chown=hermes:hermes entrypoint.sh /opt/hermes/entrypoint.sh

# MAG bundled MCP servers (stdio, zero-dependency Node). The
# MAG control plane wires them per-tenant via generated mcp_servers entries.
RUN mkdir -p /opt/mag/google-mcp /opt/mag/onedrive-mcp /opt/mag/c6-bank-mcp /opt/mag/linear-mcp /opt/mag/clickup-mcp && chown -R hermes:hermes /opt/mag
COPY --chown=hermes:hermes mcp/google/server.mjs /opt/mag/google-mcp/server.mjs
COPY --chown=hermes:hermes mcp/onedrive/server.mjs /opt/mag/onedrive-mcp/server.mjs
COPY --chown=hermes:hermes mcp/c6-bank/server.mjs /opt/mag/c6-bank-mcp/server.mjs

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

# Auxiliary (vision/compression/web_extract/...) usage ledger: capture per-call
# model+tokens for auxiliary LLM calls (which use separate models like gpt-4o)
# so the control plane meters them against the REAL model instead of losing
# them. Depends on mag_turn_ledger.py (COPY'd to /opt/hermes/agent/). See header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_aux_usage_ledger.py

# Per-tool credits: report the toolsets a turn used in agent:end, so the control
# plane can bill credits weighted by tool complexity. See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_toolsets_used.py

# Register send_message as an agent-callable tool (toolset "messaging").
# Upstream ships the send engine but deliberately never wires it into the
# agent's own tool-calling loop. See script header for the safety rationale
# and how MAG's own prompt-level policy still governs who may be messaged.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_enable_send_message.py

# Admin block: hard-stop a client-channel turn before the agent runs when staff
# has blocked this tenant from the Control Center. Highest-priority gate — runs
# before credits/topics, since a blocked tenant shouldn't even pay for those
# checks. See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_admin_block.py

# Credit hard cap (Fase 2): block client-channel turns before the agent runs when
# the tenant is out of credits, with a humane message. See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_credit_hardcap.py

# Credit warning (Fase 2): append an 80%-of-quota heads-up to the tenant's own
# reply for that turn (never a separate/proactive push). See script header.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_credit_warning.py

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

# Kill 4 more raw-diagnostic leaks to client channels (STT-unavailable install
# hints, compression-abort/aux-fallback ops notes, agent-inactivity timeout
# internals) that bypass BOTH the slash-command gate and the LLM-output
# sanitizer. See script header for why the other 4 candidate sites need no fix.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_suppress_agent_diagnostics.py

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

# MCP server pdf-tools (stdio, Python + pymupdf). Expõe extract_pdf_images e
# generate_pdf_report ao agente — necessário porque execute_code está desabilitado
# em canais cliente (WhatsApp/Telegram).
RUN mkdir -p /opt/mag/pdf-tools-mcp && chown -R hermes:hermes /opt/mag/pdf-tools-mcp
COPY --chown=hermes:hermes mcp/pdf-tools/server.py /opt/mag/pdf-tools-mcp/server.py

# Telegram pairing approval over HTTP: lets the web approve the pairing code Hermes DMs
# an un-allowlisted user (delegates to Hermes' own PairingStore). 3 thin gateway routes
# (/api/telegram/pairing[/approve|/revoke]). Anchors directly on the base api_server.py
# text — independent of any WhatsApp-related patch. See script headers.
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
# python-docx/openpyxl/python-pptx back the pdf-tools MCP's Word/Excel/PowerPoint
# readers — same "no execute_code on client channels" reasoning as PDF: without a
# dedicated MCP tool, a .docx/.xlsx/.pptx sent on Telegram/WhatsApp has no safe
# read path at all.
RUN VIRTUAL_ENV=/opt/hermes/.venv uv pip install \
    --python /opt/hermes/.venv/bin/python3 \
    pymupdf pymupdf4llm pytesseract python-docx openpyxl python-pptx

# Speech-to-text (voice messages): faster-whisper, genuinely free/offline, no API
# key, no per-message cost. Exact version pin matches upstream's own [voice]
# extra in pyproject.toml — installed directly (not via that extra) since
# sounddevice/numpy in it are for local mic capture, irrelevant to a headless
# container.
RUN VIRTUAL_ENV=/opt/hermes/.venv uv pip install --python /opt/hermes/.venv/bin/python3 faster-whisper==1.2.1

# Pre-bake the model into the IMAGE (not /opt/data) at build time: deterministic,
# zero runtime network dependency. HF_HOME is fixed to an in-image path —
# independent of the later HOME=/opt/data override for the hermes user — so
# every tenant container finds this SAME pre-warmed cache instead of
# re-downloading into its own ephemeral writable layer on first voice message.
# Runs as root (current USER); chown so the hermes user (who runs the gateway
# at runtime) can read it. Model size "small" (not the faster "base") for
# usable pt-BR accuracy on compressed/noisy Telegram/WhatsApp voice notes —
# must match config.yaml's stt.local.model (buildConfigYaml() in
# internal.service.ts) or this pre-bake is wasted and it lazy-downloads instead.
ENV HF_HOME=/opt/hermes/.cache/huggingface
RUN /opt/hermes/.venv/bin/python3 -c "from faster_whisper import WhisperModel; WhisperModel('small', device='cpu', compute_type='int8')" \
    && chown -R hermes:hermes /opt/hermes/.cache/huggingface

# MAG-bundled skills seeded into the tenant volume by entrypoint.sh.
# Skills live at runtime under /opt/data/skills/ (the tenant volume).
# Storing them here avoids losing them on image rebuild while keeping
# the seeding idempotent (entrypoint never overwrites existing skills).
RUN mkdir -p /opt/hermes/bootstrap/skills/productivity/pdf-generation \
             /opt/hermes/bootstrap/skills/productivity/ocr-and-documents
COPY --chown=hermes:hermes bootstrap/skills/productivity/pdf-generation/SKILL.md \
    /opt/hermes/bootstrap/skills/productivity/pdf-generation/SKILL.md
# Override the default ocr-and-documents skill with MAG's version that includes:
# - embedded image extraction (page.get_images + doc.extract_image)
# - tesseract OCR via get_textpage_ocr() (tesseract-ocr installed above)
# - PDF reconstruction preserving original photos
COPY --chown=hermes:hermes bootstrap/skills/productivity/ocr-and-documents/SKILL.md \
    /opt/hermes/bootstrap/skills/productivity/ocr-and-documents/SKILL.md

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

