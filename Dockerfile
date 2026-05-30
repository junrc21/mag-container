ARG BASE_IMAGE=nousresearch/hermes-agent:main
FROM ${BASE_IMAGE}

# We keep runtime as user "hermes" (no root at runtime).
# Put bootstrap assets in /opt/hermes/bootstrap (image filesystem).
USER root

# Make ByteRover tool timeouts configurable via env vars (so we never have to
# hot-edit the container filesystem):
# - HERMES_BYTEROVER_QUERY_TIMEOUT_SECONDS (default 10)
# - HERMES_BYTEROVER_CURATE_TIMEOUT_SECONDS (default 120)
# - HERMES_BYTEROVER_STATUS_TIMEOUT_SECONDS (default 60)
RUN /opt/hermes/.venv/bin/python3 - <<'PY'\nimport pathlib, re\n\np = pathlib.Path('/opt/hermes/plugins/memory/byterover/__init__.py')\nif not p.exists():\n    raise SystemExit('ByteRover plugin not found at ' + str(p))\n\ntext = p.read_text()\n\n# Ensure `import os` exists.\nif not re.search(r'^\\s*import\\s+os\\s*$', text, flags=re.M):\n    m = re.search(r'^(import\\s+[^\\n]+\\n)+', text, flags=re.M)\n    if not m:\n        raise SystemExit('Could not locate imports block to insert import os')\n    text = text[:m.end()] + 'import os\\n' + text[m.end():]\n\n# Make constants env-driven.\ntext2 = re.sub(\n    r'^_QUERY_TIMEOUT\\s*=.*$',\n    '_QUERY_TIMEOUT = int(os.getenv(\"HERMES_BYTEROVER_QUERY_TIMEOUT_SECONDS\", \"10\"))  # brv query',\n    text,\n    flags=re.M,\n    count=1,\n)\ntext2 = re.sub(\n    r'^_CURATE_TIMEOUT\\s*=.*$',\n    '_CURATE_TIMEOUT = int(os.getenv(\"HERMES_BYTEROVER_CURATE_TIMEOUT_SECONDS\", \"120\"))  # brv curate',\n    text2,\n    flags=re.M,\n    count=1,\n)\n\n# Make status timeout env-driven (hardcoded 15s is too low on busy queues).\ntext2 = text2.replace(\n    'result = _run_brv([\"status\"], timeout=15, cwd=self._cwd)',\n    'result = _run_brv([\"status\"], timeout=int(os.getenv(\"HERMES_BYTEROVER_STATUS_TIMEOUT_SECONDS\", \"60\")), cwd=self._cwd)',\n    1,\n)\n\np.write_text(text2)\nprint('OK: patched', p)\nPY

RUN mkdir -p /opt/hermes/bootstrap && chown -R hermes:hermes /opt/hermes/bootstrap

COPY --chown=hermes:hermes bootstrap/config.yaml /opt/hermes/bootstrap/config.yaml
COPY --chown=hermes:hermes bootstrap/soul.md /opt/hermes/bootstrap/soul.md
COPY --chown=hermes:hermes entrypoint.sh /opt/hermes/entrypoint.sh

RUN chmod +x /opt/hermes/entrypoint.sh

USER hermes
ENV HOME=/opt/data

ENTRYPOINT ["/opt/hermes/entrypoint.sh"]
