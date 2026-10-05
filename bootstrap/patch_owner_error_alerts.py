"""Aplicar depois dos patches de sanitização. Erros operacionais vão só ao responsável."""
import ast
import os
from pathlib import Path

MARKER = '# MAG_OWNER_ERROR_ALERTS_V1'
RUN = Path(os.getenv('GATEWAY_RUN_PY', '/opt/hermes/gateway/run.py'))
BASE = Path(os.getenv('GATEWAY_BASE_PY', '/opt/hermes/gateway/platforms/base.py'))
CRON = Path(os.getenv('CRON_SCHEDULER_PY', '/opt/hermes/cron/scheduler.py'))


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise SystemExit('owner-alerts: anchor drift: ' + old[:80])
    return text.replace(old, new, 1)


def replace_function(text, name, transform):
    nodes = [n for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(nodes) != 1:
        raise SystemExit('owner-alerts: function drift: ' + name)
    node = nodes[0]
    lines = text.splitlines(keepends=True)
    old = ''.join(lines[node.lineno-1:node.end_lineno])
    return ''.join(lines[:node.lineno-1]) + transform(old) + ''.join(lines[node.end_lineno:])


def gateway(text):
    if 'MAG: map raw provider/API errors to humane pt-BR copy' not in text:
        raise SystemExit('owner-alerts: apply patch_gateway_output first')
    text = replace_function(text, '_gateway_provider_error_reply', lambda old: '''def _gateway_provider_error_reply(text: str, platform=None) -> str:
    # A policy refusal remains an ordinary answer, not an operational alert.
    if _GATEWAY_PROVIDER_POLICY_RE.search(text) and not _GATEWAY_AUTH_ERROR_RE.search(text):
        return "Não consigo seguir com esse pedido específico. Se quiser, me peça de outro jeito."
    _mag_owner_alerts.report('provider', platform)
    return ""
''')
    text = replace_once(text, 'return _gateway_provider_error_reply(redacted)', 'return _gateway_provider_error_reply(redacted, platform)')
    text = replace_once(text, 'return _gateway_provider_error_reply(text)', 'return _gateway_provider_error_reply(text, platform) or None')
    text = replace_once(text, '        return _MAG_GENERIC_FAILURE_REPLY', "        _mag_owner_alerts.report('tooling', platform)\n        return ''")
    text = replace_once(text, '    redacted = _redact_gateway_user_facing_secrets(str(text))\n',
        "    redacted = _redact_gateway_user_facing_secrets(str(text))\n    if _mag_owner_alerts.operational_error(redacted):\n        _mag_owner_alerts.report('generation', platform)\n        return ''\n")
    def status(old):
        return replace_once(old, '    text = _redact_gateway_user_facing_secrets(text)\n', "    text = _redact_gateway_user_facing_secrets(text)\n    if event_type in {'error', 'critical'}:\n        _mag_owner_alerts.report('generation', platform)\n        return None\n")
    text = replace_function(text, '_prepare_gateway_status_message', status)
    def notice(old):
        return replace_once(old, '                try:\n', "                level = str(getattr(getattr(notice, 'level', None), 'value', getattr(notice, 'level', ''))).lower()\n                if _mag_owner_alerts.customer_channel(source.platform) and (level in {'error', 'critical'} or _mag_owner_alerts.operational_error(getattr(notice, 'text', ''))):\n                    _mag_owner_alerts.report('generation', source.platform)\n                    return\n                try:\n")
    text = replace_function(text, '_notice_callback_sync', notice)
    return replace_once(text, 'def _gateway_provider_error_reply(', MARKER + '\nimport mag_owner_alerts as _mag_owner_alerts\n\ndef _gateway_provider_error_reply(')


def base(text):
    text = replace_once(text, 'import asyncio\n', 'import asyncio\n' + MARKER + '\nimport mag_owner_alerts as _mag_owner_alerts\n')
    def delivery(old):
        return replace_once(old, '\n        error_str = result.error or ""\n', '''
        # Do not send a technical notice or reformat a possibly delivered reply.
        if _mag_owner_alerts.customer_channel(self.platform):
            _mag_owner_alerts.report('delivery', self.platform)
            return result
        error_str = result.error or ""
''')
    text = replace_function(text, '_send_with_retry', delivery)
    def background(old):
        start = old.index("            # Send the error to the user so they aren't left with radio silence\n")
        end = old.index('        finally:\n', start)
        original = old[start:end]
        guarded = "            if _mag_owner_alerts.customer_channel(self.platform):\n                _mag_owner_alerts.report('generation', self.platform)\n            else:\n" + ''.join('    '+line if line.strip() else line for line in original.splitlines(keepends=True))
        return old[:start] + guarded + old[end:]
    text = replace_function(text, '_process_message_background', background)
    anchor = '        hook = getattr(self, hook_name, None)\n'
    return replace_once(text, anchor, '''        if hook_name == 'on_processing_complete' and len(args) > 1 and args[1] == ProcessingOutcome.FAILURE:
            _mag_owner_alerts.report('generation', self.platform)
''' + anchor)


def cron(text):
    text = replace_function(text, '_mag_cron_failure_message', lambda old: '''def _mag_cron_failure_message(job, error) -> str:
    _mag_owner_alerts.report('routine', 'routine')
    return ""  # tick's should_deliver gate skips channel/companion delivery.
''')
    return replace_once(text, 'def _mag_cron_failure_message(', MARKER + '\nimport mag_owner_alerts as _mag_owner_alerts\n\ndef _mag_cron_failure_message(')


def main():
    changes = []
    for path, transform in [(RUN, gateway), (BASE, base), (CRON, cron)]:
        source = path.read_text()
        if MARKER in source:
            continue
        patched = transform(source)
        compile(patched, str(path), 'exec')
        changes.append((path, patched))
    for path, patched in changes:
        path.write_text(patched)
    print('OK: owner-only error alerts; patched files:', len(changes))

if __name__ == '__main__': main()
