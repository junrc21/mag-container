"""Build-time patch: block restricted topics on client channels before the LLM runs.

The MAG control plane writes `~/policy/forbidden-topics.json` with per-tenant topic
rules. Each rule contains keyword/phrase matches plus optional sender exceptions:
  - Telegram: exact user IDs
  - WhatsApp: numbers (JIDs are normalized to digits)

When an inbound client-channel message matches a restricted topic and the sender is
NOT in that rule's allowlist, the gateway returns a humane refusal immediately,
without spending an LLM turn. Internal staff surfaces stay untouched.

Idempotent + fail-loud (mirrors the other bootstrap patches).
"""

import os
import pathlib

RUN_PY = pathlib.Path(os.getenv("GATEWAY_RUN_PY", "/opt/hermes/gateway/run.py"))

MARKER = "_mag_forbidden_topic_block_message"

HELPERS_ANCHOR = "def _gateway_platform_value(platform: Any) -> str:"
HELPERS = '''# MAG: restricted-topics gate. Client-channel turns that match a tenant-defined
# blocked topic are refused BEFORE the model runs, unless the sender is explicitly
# allowlisted for that topic (Telegram user ID / WhatsApp number).
_MAG_FORBIDDEN_TOPICS_PATH = os.path.expanduser("~/policy/forbidden-topics.json")
_MAG_FORBIDDEN_TOPICS_CACHE = {"mtime": None, "policies": []}
_MAG_FORBIDDEN_TOPICS_DEFAULT_REPLY = (
    "Sobre esse assunto eu nao posso falar por aqui."
)


def _mag_normalize_topic_text(value: Any) -> str:
    try:
        import re as _re
        import unicodedata as _ud
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = _ud.normalize("NFKD", text)
        text = "".join(ch for ch in text if not _ud.combining(ch))
        text = _re.sub(r"\\s+", " ", text)
        return text.strip()
    except Exception:
        return str(value or "").strip().lower()


def _mag_normalize_whatsapp_sender(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    if "@" in raw:
        raw = raw.split("@", 1)[0]
    digits = "".join(ch for ch in raw if ch.isdigit())
    return digits or raw


def _mag_load_forbidden_policies():
    cache = _MAG_FORBIDDEN_TOPICS_CACHE
    try:
        if not os.path.exists(_MAG_FORBIDDEN_TOPICS_PATH):
            cache["mtime"] = None
            cache["policies"] = []
            return []
        mtime = os.path.getmtime(_MAG_FORBIDDEN_TOPICS_PATH)
        if cache.get("mtime") == mtime:
            return cache.get("policies") or []
        import json as _json
        with open(_MAG_FORBIDDEN_TOPICS_PATH, encoding="utf-8") as f:
            data = _json.load(f)
        out = []
        for item in (data.get("policies") or []):
            if not isinstance(item, dict):
                continue
            if item.get("enabled") is False:
                continue
            terms = []
            seen_terms = set()
            for raw_term in (item.get("matchTerms") or []):
                term = _mag_normalize_topic_text(raw_term)
                if not term or term in seen_terms:
                    continue
                seen_terms.add(term)
                terms.append(term)
            if not terms:
                continue
            tg_ids = []
            seen_tg = set()
            for raw_id in (item.get("allowedTelegramUserIds") or []):
                value = str(raw_id or "").strip()
                if not value or value in seen_tg:
                    continue
                seen_tg.add(value)
                tg_ids.append(value)
            wa_numbers = []
            seen_wa = set()
            for raw_number in (item.get("allowedWhatsappNumbers") or []):
                value = _mag_normalize_whatsapp_sender(raw_number)
                if not value or value in seen_wa:
                    continue
                seen_wa.add(value)
                wa_numbers.append(value)
            refusal = str(item.get("refusalMessage") or "").strip() or _MAG_FORBIDDEN_TOPICS_DEFAULT_REPLY
            out.append({
                "label": str(item.get("label") or "").strip(),
                "mode": str(item.get("mode") or "both").strip() or "both",
                "matchTerms": terms,
                "allowedTelegramUserIds": tg_ids,
                "allowedWhatsappNumbers": wa_numbers,
                "refusalMessage": refusal,
            })
        cache["mtime"] = mtime
        cache["policies"] = out
        return out
    except Exception:
        return cache.get("policies") or []


def _mag_sender_allowed_for_policy(source, policy) -> bool:
    try:
        from gateway.session_context import get_session_env
        platform_name = source.platform.value if source and getattr(source, "platform", None) else ""
        if platform_name == "telegram":
            sender = (
                get_session_env("HERMES_SESSION_USER_ID", "")
                or str(getattr(source, "user_id", "") or "").strip()
                or get_session_env("HERMES_SESSION_CHAT_ID", "")
            )
            return str(sender).strip() in set(policy.get("allowedTelegramUserIds") or [])
        if platform_name == "whatsapp":
            sender = (
                get_session_env("HERMES_SESSION_USER_ID", "")
                or str(getattr(source, "user_id", "") or "").strip()
                or get_session_env("HERMES_SESSION_CHAT_ID", "")
            )
            return _mag_normalize_whatsapp_sender(sender) in set(policy.get("allowedWhatsappNumbers") or [])
    except Exception:
        return False
    return False


def _mag_forbidden_topic_block_message(source, event):
    try:
        platform_name = source.platform.value if source and getattr(source, "platform", None) else ""
        if platform_name in ("api_server", "local", "cli"):
            return None
        text = str(getattr(event, "text", "") or "").strip()
        if not text:
            return None
        normalized_text = _mag_normalize_topic_text(text)
        if not normalized_text:
            return None
        for policy in _mag_load_forbidden_policies():
            terms = policy.get("matchTerms") or []
            if not any(term and term in normalized_text for term in terms):
                continue
            if _mag_sender_allowed_for_policy(source, policy):
                continue
            return policy.get("refusalMessage") or _MAG_FORBIDDEN_TOPICS_DEFAULT_REPLY
    except Exception:
        return None
    return None


'''

GATE_ANCHOR = "        self._running_agents[_quick_key] = _AGENT_PENDING_SENTINEL\n"
GATE_BLOCK = (
    "        # MAG: restricted-topics gate - block sensitive tenant-defined themes\\n"
    "        # before the model runs, unless this sender is explicitly allowlisted.\\n"
    "        _mag_topic_block = _mag_forbidden_topic_block_message(source, event)\\n"
    "        if _mag_topic_block is not None:\\n"
    "            return _mag_topic_block\\n"
)


def main() -> None:
    if not RUN_PY.exists():
        raise SystemExit(f"gateway run.py not found at {RUN_PY}")
    text = RUN_PY.read_text()

    if MARKER in text:
        print("OK: forbidden-topics gate already patched (idempotent no-op)")
        return

    if HELPERS_ANCHOR not in text:
        raise SystemExit("patch_forbidden_topics_gate: helpers anchor missing (Hermes changed).")
    text = text.replace(HELPERS_ANCHOR, HELPERS + HELPERS_ANCHOR, 1)

    if GATE_ANCHOR not in text:
        raise SystemExit("patch_forbidden_topics_gate: gate anchor missing (Hermes changed).")
    text = text.replace(GATE_ANCHOR, GATE_BLOCK + GATE_ANCHOR, 1)

    RUN_PY.write_text(text)
    print("OK: patched restricted-topics pre-turn gate")


if __name__ == "__main__":
    main()
