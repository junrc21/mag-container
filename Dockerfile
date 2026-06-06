ARG BASE_IMAGE=nousresearch/hermes-agent:main
FROM ${BASE_IMAGE}

# We keep runtime as user "hermes" (no root at runtime).
# Put bootstrap assets in /opt/hermes/bootstrap (image filesystem).
USER root

RUN mkdir -p /opt/hermes/bootstrap && chown -R hermes:hermes /opt/hermes/bootstrap

COPY --chown=hermes:hermes bootstrap/config.yaml /opt/hermes/bootstrap/config.yaml
COPY --chown=hermes:hermes bootstrap/soul.md /opt/hermes/bootstrap/soul.md
COPY --chown=hermes:hermes bootstrap/patch_byterover_plugin.py /opt/hermes/bootstrap/patch_byterover_plugin.py
COPY --chown=hermes:hermes entrypoint.sh /opt/hermes/entrypoint.sh

# MAG Google Workspace MCP server (stdio, zero-dependency Node). The MAG control
# plane wires it per-tenant via mcp_servers.google in the generated config.yaml.
RUN mkdir -p /opt/mag/google-mcp && chown -R hermes:hermes /opt/mag
COPY --chown=hermes:hermes mcp/google/server.mjs /opt/mag/google-mcp/server.mjs

# MAG Document Reader MCP (stdio, Node + pdf/docx/xlsx libs). Extracts text from
# uploaded documents so the agent can absorb it into ByteRover. Deps are installed
# at build time (no network needed at runtime).
RUN mkdir -p /opt/mag/docreader
COPY mcp/docreader/package.json /opt/mag/docreader/package.json
RUN cd /opt/mag/docreader && npm install --omit=dev --no-audit --no-fund && chown -R hermes:hermes /opt/mag/docreader
COPY --chown=hermes:hermes mcp/docreader/server.mjs /opt/mag/docreader/server.mjs

# Pin the ByteRover CLI to 3.14.0. Newer 3.16.x REGRESSED curate: the
# intermittent "empty Gemini candidate" error became FATAL to the write (so
# memory silently never persists), and HITL review defaults ON (only stages
# curations). 3.14.0 persists reliably (verified 3/3 vs ~0/5 on 3.16.1). Baked
# into the image so it wins over any volume-installed copy via PATH + BRV_BIN.
RUN npm install --prefix /opt/mag/brv-cli byterover-cli@3.14.0 --no-audit --no-fund \
    && chown -R hermes:hermes /opt/mag/brv-cli

RUN /opt/hermes/.venv/bin/python3 /opt/hermes/bootstrap/patch_byterover_plugin.py

RUN chmod +x /opt/hermes/entrypoint.sh

USER hermes
ENV HOME=/opt/data
ENV XDG_DATA_HOME=/opt/data/.local/share
ENV XDG_CONFIG_HOME=/opt/data/.config
ENV BRV_INSTALL_DIR=/opt/data/.local/share/brv-cli
ENV PATH=/opt/data/.local/share/brv-cli/bin:/opt/hermes/bin:/opt/hermes/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

ENTRYPOINT ["/opt/hermes/entrypoint.sh"]
