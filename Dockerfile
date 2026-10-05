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

# Agrupa cópias para manter a imagem abaixo do limite de camadas do Docker.
COPY --chown=hermes:hermes \
    bootstrap/config.yaml \
    bootstrap/soul.md \
    bootstrap/patch_byterover_plugin.py \
    bootstrap/patch_gateway_output.py \
    bootstrap/patch_no_review_delivery.py \
    bootstrap/patch_gateway_system_copy.py \
    bootstrap/patch_approval_async.py \
    bootstrap/patch_channel_noise_suppress.py \
    bootstrap/patch_disable_channel_commands.py \
    bootstrap/patch_usage_tokens.py \
    bootstrap/patch_aux_usage_ledger.py \
    bootstrap/patch_toolsets_used.py \
    bootstrap/patch_enable_send_message.py \
    bootstrap/patch_admin_block.py \
    bootstrap/patch_credit_hardcap.py \
    bootstrap/patch_tool_credit_gate.py \
    bootstrap/patch_credit_warning.py \
    bootstrap/patch_companion_credit_gate.py \
    bootstrap/patch_forbidden_topics_gate.py \
    bootstrap/patch_cron_job_runs.py \
    bootstrap/patch_cron_credit_charge.py \
    bootstrap/patch_disable_channel_code_exec.py \
    bootstrap/patch_suppress_reset_banner.py \
    bootstrap/patch_suppress_agent_diagnostics.py \
    /opt/hermes/bootstrap/
COPY --chown=hermes:hermes bootstrap/mag_turn_ledger.py /opt/hermes/agent/mag_turn_ledger.py
# Compartilhado pelo gate do gateway e pelo agendador de rotinas. Enquanto a regra
# existia só dentro do patch do gateway, as rotinas de um tenant bloqueado
# continuavam rodando — e entregando mensagem no canal dele todo dia.
COPY --chown=hermes:hermes bootstrap/mag_block_guard.py /opt/hermes/mag_block_guard.py
COPY --chown=hermes:hermes bootstrap/mag_credit_guard.py /opt/hermes/mag_credit_guard.py
# A recusa POR FERRAMENTA. O hardcap acima pergunta "sobrou alguma coisa?";
# este pergunta "da para pagar ISTO?", no unico instante em que o preco e
# conhecido e a acao ainda nao aconteceu.
COPY --chown=hermes:hermes entrypoint.sh /opt/hermes/entrypoint.sh
RUN python3 - <<'PY'
from pathlib import Path
path = Path('/opt/hermes/entrypoint.sh')
data = path.read_bytes().replace(b'\r\n', b'\n')
path.write_bytes(data)
PY

# MAG bundled MCP servers (stdio, zero-dependency Node). The
# MAG control plane wires them per-tenant via generated mcp_servers entries.
RUN mkdir -p /opt/mag/google-mcp /opt/mag/onedrive-mcp /opt/mag/c6-bank-mcp /opt/mag/linear-mcp /opt/mag/clickup-mcp /opt/mag/mercado-livre-mcp /opt/mag/investing-mcp /opt/mag/teammates-mcp /opt/mag/helpcenter-mcp /opt/mag/mag-ops-mcp /opt/mag/companion-browser-mcp && chown -R hermes:hermes /opt/mag
COPY --chown=hermes:hermes mcp/google/server.mjs /opt/mag/google-mcp/server.mjs
COPY --chown=hermes:hermes mcp/onedrive/server.mjs /opt/mag/onedrive-mcp/server.mjs
COPY --chown=hermes:hermes mcp/c6-bank/server.mjs /opt/mag/c6-bank-mcp/server.mjs

# MAG Linear + ClickUp MCP servers (stdio, zero-dependency Node). Use the connector
# token the user authorized in Fontes (fetched per-call from the MAG control plane).
COPY --chown=hermes:hermes mcp/linear/server.mjs /opt/mag/linear-mcp/server.mjs
COPY --chown=hermes:hermes mcp/clickup/server.mjs /opt/mag/clickup-mcp/server.mjs
COPY --chown=hermes:hermes mcp/mercado-livre/server.mjs /opt/mag/mercado-livre-mcp/server.mjs
# MAG Investing: CVM regulatory data and optional B3 historical-market MCP. It talks only to the MAG control
# plane; the external service Bearer token never enters the tenant runtime image.
COPY --chown=hermes:hermes mcp/investing/server.mjs /opt/mag/investing-mcp/server.mjs
# MAG Teammates: internal tenant roster relay (list_teammates/message_teammate) —
# lets the agent message other employees of the same tenant via their own MAG
# (Companion, Telegram, WhatsApp). Different from send_message's external
# channel_directory contacts.
COPY --chown=hermes:hermes mcp/teammates/server.mjs /opt/mag/teammates-mcp/server.mjs
# MAG Companion Browser: controla o navegador REAL do computador do cliente (Mac/
# Windows), com a sessão dele já logada, via o app MAG Companion — NUNCA fala com o
# Companion direto, só chama de volta o control plane, que mantém o canal (WebSocket)
# de verdade. Nome deliberadamente diferente do toolset nativo 'browser' (Playwright
# sandboxed dentro deste container, sem sessão do cliente) — são coisas fisicamente
# diferentes, e o nome não pode confundir quem administra.
COPY --chown=hermes:hermes mcp/companion-browser/server.mjs /opt/mag/companion-browser-mcp/server.mjs
# MAG Help Center: search_help/read_help_page contra a central de ajuda pública do produto.
# Antes o agente tinha os links dos guias no SOUL e nenhuma forma de abri-los — sabia pra
# onde apontar sem saber o que estava escrito lá, então ou respondia raso ou improvisava um
# passo a passo que envelhecia junto com o produto. Só precisa de MAG_DOC_URL.
COPY --chown=hermes:hermes mcp/helpcenter/server.mjs /opt/mag/helpcenter-mcp/server.mjs
# MAG de Operação. Vai na imagem de TODO container, mas só é REGISTRADO no config.yaml do
# tenant de staff (ver internal.service.ts) — e, mesmo se alguém o registrasse à força, o
# servidor exige a MAG_OPS_KEY, que só existe no .env da staff. Duas travas, nenhuma
# dependendo de o container dizer a verdade sobre quem é.
COPY --chown=hermes:hermes mcp/mag-ops/server.mjs /opt/mag/mag-ops-mcp/server.mjs

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

# Atendimento compartilhado oficial do WhatsApp. Gate na entrada e proxy de todos
# os envios /messages para serializar a entrega com a ação humana de assumir.
COPY --chown=hermes:hermes bootstrap/mag_whatsapp_handoff.py /opt/hermes/mag_whatsapp_handoff.py
COPY --chown=hermes:hermes bootstrap/mag_whatsapp_resume.py /opt/hermes/mag_whatsapp_resume.py
COPY --chown=hermes:hermes \
    bootstrap/patch_whatsapp_handoff.py \
    bootstrap/patch_whatsapp_resume.py \
    bootstrap/patch_session_channel_id.py \
    bootstrap/patch_multi_whatsapp_cloud.py \
    /opt/hermes/bootstrap/

# Apply all source-code patches in one layer. Splitting every idempotent patch
# into its own RUN pushed the final image past Docker's overlay max-depth limit.
RUN set -eux; \
    for patch in \
        patch_whatsapp_handoff.py \
        patch_whatsapp_resume.py \
        patch_session_channel_id.py \
        patch_multi_whatsapp_cloud.py \
        patch_byterover_plugin.py \
        patch_gateway_output.py \
        patch_no_review_delivery.py \
        patch_gateway_system_copy.py \
        patch_approval_async.py \
        patch_channel_noise_suppress.py \
        patch_disable_channel_commands.py \
        patch_usage_tokens.py \
        patch_aux_usage_ledger.py \
        patch_toolsets_used.py \
        patch_enable_send_message.py \
        patch_admin_block.py \
        patch_credit_hardcap.py \
        patch_tool_credit_gate.py \
        patch_credit_warning.py \
        patch_companion_credit_gate.py \
        patch_forbidden_topics_gate.py \
        patch_cron_job_runs.py \
        patch_cron_credit_charge.py \
        patch_disable_channel_code_exec.py \
        patch_suppress_reset_banner.py \
        patch_suppress_agent_diagnostics.py; \
    do \
        /opt/hermes/.venv/bin/python3 "/opt/hermes/bootstrap/$patch"; \
    done

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
             /opt/hermes/bootstrap/skills/productivity/ocr-and-documents \
             /opt/hermes/bootstrap/skills/finance/excel-author \
             /opt/hermes/bootstrap/skills/productivity/docx-author
COPY --chown=hermes:hermes bootstrap/skills/productivity/pdf-generation/SKILL.md \
    /opt/hermes/bootstrap/skills/productivity/pdf-generation/SKILL.md
# Override the default ocr-and-documents skill with MAG's version that includes:
# - embedded image extraction (page.get_images + doc.extract_image)
# - tesseract OCR via get_textpage_ocr() (tesseract-ocr installed above)
# - PDF reconstruction preserving original photos
COPY --chown=hermes:hermes bootstrap/skills/productivity/ocr-and-documents/SKILL.md \
    /opt/hermes/bootstrap/skills/productivity/ocr-and-documents/SKILL.md
# Excel generation: base image ships this under optional-skills/finance/ (disabled
# by default) written for a different product (Cowork/Office-JS branches). This is
# the same content adapted for MAG's headless runtime — /opt/data/workspace output
# path instead of ./out/, no LibreOffice recalc step (not installed in this image),
# delivered via the MEDIA: convention. openpyxl is already installed (see the
# python-docx/openpyxl/python-pptx pip install above), no separate setup needed.
COPY --chown=hermes:hermes bootstrap/skills/finance/excel-author/SKILL.md \
    /opt/hermes/bootstrap/skills/finance/excel-author/SKILL.md
# Word (.docx) generation: no equivalent skill exists anywhere in the base image
# (unlike PDF/Excel/PowerPoint). Written from scratch for MAG, using python-docx
# (already installed, same install line as above) — same MEDIA: delivery
# convention as every other MAG document-generation skill.
COPY --chown=hermes:hermes bootstrap/skills/productivity/docx-author/SKILL.md \
    /opt/hermes/bootstrap/skills/productivity/docx-author/SKILL.md

# Timezone: the whole platform runs on Brasília time. HERMES_TIMEZONE is read by
# hermes_time.now() (the clock behind cron schedules + delivery), TZ covers OS-level
# time. tzdata in the venv guarantees ZoneInfo("America/Sao_Paulo") resolves even if
# the base OS ships no zoneinfo. The per-tenant generated config/.env also set these
# (control plane), so a reload keeps them; this is the image-level default.
RUN VIRTUAL_ENV=/opt/hermes/.venv uv pip install --python /opt/hermes/.venv/bin/python3 tzdata

# Keep the early Mercado Livre MCP layer identical to the last known-good image and apply
# feature changes only at the end of the build, minimizing downstream layer churn.
COPY --chown=hermes:hermes mcp/mercado-livre/server.overlay.mjs /opt/mag/mercado-livre-mcp/server.mjs

# Outlook Mail.Send provenance: bind the authenticated inbound chat turn to the
# exact MCP send arguments. The model cannot provide or override this proof.
COPY --chown=hermes:hermes \
    bootstrap/patch_outlook_send_provenance.py \
    bootstrap/patch_cron_companion_delivery.py \
    bootstrap/patch_owner_error_alerts.py \
    /opt/hermes/bootstrap/
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_outlook_send_provenance.py

# A scheduled routine can now deliver to the MAG Companion (`deliver="companion:<id>"`).
# The Companion is not a Hermes platform — the mag-api is what authenticates the device —
# so delivery goes out over HTTP to the control plane instead of through an adapter.
#
# Order: AFTER patch_cron_job_runs and patch_sanitize_cron_errors, which also edit
# cron/scheduler.py. The anchors do not collide (those target `tick()` and
# `deliver_content`; this one targets `_deliver_result`), but a deterministic order
# protects against future churn.
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_cron_companion_delivery.py

# Última barreira: erros de canal/rotina vão exclusivamente ao painel do responsável.
COPY --chown=hermes:hermes bootstrap/mag_owner_alerts.py /opt/hermes/mag_owner_alerts.py
RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_owner_error_alerts.py \
    && chmod +x /opt/hermes/entrypoint.sh

USER hermes
ENV TZ=America/Sao_Paulo
ENV HERMES_TIMEZONE=America/Sao_Paulo
ENV HOME=/opt/data
ENV XDG_DATA_HOME=/opt/data/.local/share
ENV XDG_CONFIG_HOME=/opt/data/.config
ENV BRV_INSTALL_DIR=/opt/data/.local/share/brv-cli
ENV PATH=/opt/data/.local/share/brv-cli/bin:/opt/hermes/bin:/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

ENTRYPOINT ["/opt/hermes/entrypoint.sh"]
