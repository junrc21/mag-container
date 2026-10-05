"""Alertas operacionais somente no painel do responsável. Sem texto de conversa/segredos."""
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import urllib.request

log = logging.getLogger(__name__)
_lock = threading.Lock()
_wakeup = threading.Event()
_thread = None
_last = {}
CATEGORIES = {'provider', 'generation', 'delivery', 'tooling', 'routine'}
INTERNAL = {'api_server', 'local', 'cli'}


def customer_channel(platform):
    return str(getattr(platform, 'value', platform) or 'unknown').lower() not in INTERNAL


def operational_error(text):
    return bool(re.search(r'^(?:tive|ocorreu|houve|encontrei|enfrentei)\b.{0,80}erro cr[íi]tico|(?:contate|fale com|contato com) o suporte da CyriusX', str(text), re.I))


def _directory():
    return Path(os.environ.get('HERMES_HOME', '/opt/data')) / 'mag-owner-alerts'


def _flush():
    base = os.environ.get('MAG_API_URL', '').rstrip('/')
    key = os.environ.get('MAG_INTERNAL_KEY') or os.environ.get('MAG_API_INTERNAL_KEY')
    tenant = os.environ.get('MAG_TENANT_ID')
    if not base or not key or not tenant:
        return
    for path in list(_directory().glob('*.json'))[:100]:
        try:
            with _lock:
                body = json.loads(path.read_text())
            # Rebuild payload from a fixed schema: spool never transmits exception text.
            payload = { 'tenantId': tenant, 'category': body['category'], 'platform': body['platform'] }
            request = urllib.request.Request(base + '/internal/runtime-alerts',
                data=json.dumps(payload).encode(), headers={'Content-Type':'application/json', 'x-internal-key':key}, method='POST')
            with urllib.request.urlopen(request, timeout=5) as response:
                accepted = json.loads(response.read(4096)).get('accepted') is True
            if accepted:
                with _lock:
                    if path.exists() and json.loads(path.read_text()) == body:
                        path.unlink()  # Only this helper's acknowledged alert, never tenant data.
        except Exception:
            log.warning('Owner alert pending; retrying privately')


def _worker():
    while True:
        _wakeup.wait(30)
        _wakeup.clear()
        try:
            _flush()
        except Exception:
            log.warning("Owner alert worker will retry")


def _start():
    global _thread
    with _lock:
        if _thread is None and os.environ.get('MAG_API_URL') and os.environ.get('MAG_TENANT_ID'):
            _thread = threading.Thread(target=_worker, name='mag-owner-alerts', daemon=True)
            _thread.start()


def report(category, platform=None):
    """Persist locally, deliver off-thread. API outage never falls back to the customer."""
    if not customer_channel(platform):
        return
    # A suppressed failure must not finish a WhatsApp batch as a successful reply.
    import sys
    resume = sys.modules.get('mag_whatsapp_resume')
    state = resume.turn.get() if resume else None
    if state is not None:
        state['failed'] = True
    category = category if category in CATEGORIES else 'generation'
    platform = str(getattr(platform, 'value', platform) or 'unknown').lower()
    if not re.fullmatch(r'[a-z_]{1,32}', platform):
        platform = 'unknown'
    identity = category + '-' + platform
    try:
        with _lock:
            now = time.monotonic()
            if now - _last.get(identity, -1000) < 60:
                return
            directory = _directory()
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / (identity + '.json')
            if not target.exists():
                temporary = directory / (identity + '.tmp')
                temporary.write_text(json.dumps({'category':category,'platform':platform}))
                os.replace(temporary, target)
            _last[identity] = now
        _start()
        _wakeup.set()
    except Exception:
        log.error('Unable to persist owner alert; customer notification suppressed')


_start()  # Recovers persisted alerts after a restart, even without another error.
