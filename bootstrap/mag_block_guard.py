"""Authoritative MAG access guard, shared by the chat and cron runtime paths.

Answers one question: *has staff cut this tenant's access from the Control
Center?* Both the gateway turn gate and the cron scheduler ask it, and they must
answer identically — the previous version only existed inside the gateway patch,
which is exactly why routines kept running (and delivering) for blocked tenants.

## The outage question, and why it is not fail-open

The first version allowed the turn on any error, arguing that a control-plane
hiccup should not lock every client out of their own MAG. True — and the cost
was the mirror failure: a blocked tenant stayed served for as long as the API
was unreachable.

Block state is *sticky*: it changes about once a month, not once a turn. So it
can be remembered on disk, and the dilemma disappears:

  * API answers            -> use it, and remember it
  * API silent, cache warm -> use the last known answer. An outage neither
                              blocks nor unblocks anyone.
  * API silent, no cache   -> BLOCKED

The last case only affects a container that has never once reached the control
plane. Serving a tenant we were never able to verify is the single case where
being wrong has no ceiling.

## The reason is never returned

Product decision, explicit: why a tenant was blocked is CyriusX-internal. The
API returns `reason`; this module drops it on purpose and exposes only a
boolean. Nothing downstream can leak what it never receives.
"""

from dataclasses import dataclass
from enum import Enum
import json
import os
import time
from typing import Optional, Tuple
from urllib.parse import quote
from urllib.request import Request, urlopen


class AccessStatus(Enum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class AccessCheck:
    status: AccessStatus
    #: True when the answer came from the on-disk memory instead of the API.
    from_cache: bool = False


# Under /opt/data (HERMES_HOME) so it survives a container recreate — a recreate
# must not hand a blocked tenant a clean slate.
CACHE_PATH = os.getenv("MAG_BLOCK_CACHE", "/opt/data/.mag_block.json")

# How long a remembered answer is trusted once the API goes quiet. Generous,
# because block state barely moves; bounded, because "never verified again" is
# not a state a container should sit in indefinitely.
CACHE_MAX_AGE_S = 24 * 60 * 60


def _cache_write(blocked: bool) -> None:
    try:
        with open(CACHE_PATH, "w") as fh:
            json.dump({"blocked": bool(blocked), "at": time.time()}, fh)
    except Exception:
        pass  # the cache is an optimization; never break a turn over it


def _cache_read(now: Optional[float] = None) -> Tuple[Optional[bool], bool]:
    """(blocked, fresh) from the last successful check, or (None, False)."""
    try:
        with open(CACHE_PATH) as fh:
            data = json.load(fh)
        blocked = data.get("blocked")
        if not isinstance(blocked, bool):
            return None, False
        age = (now if now is not None else time.time()) - float(data.get("at") or 0)
        return blocked, age <= CACHE_MAX_AGE_S
    except Exception:
        return None, False


def check_tenant_access(timeout_seconds: float = 4.0) -> AccessCheck:
    """Can this tenant act right now? See the module docstring for the rules."""
    api_url = (os.getenv("MAG_API_URL") or "").rstrip("/")
    internal_key = os.getenv("MAG_INTERNAL_KEY") or os.getenv("MAG_API_INTERNAL_KEY") or ""
    tenant_slug = os.getenv("MAG_TENANT_SLUG") or ""

    # Missing configuration is not an outage: a container that cannot identify
    # itself cannot prove it is allowed to serve.
    if not api_url or not internal_key or not tenant_slug:
        return AccessCheck(AccessStatus.BLOCKED)

    try:
        request = Request(
            f"{api_url}/internal/runtime/{quote(tenant_slug, safe='')}/blocked",
            headers={"x-internal-key": internal_key},
            method="GET",
        )
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        blocked = payload.get("blocked")
        if not isinstance(blocked, bool):
            raise ValueError("malformed payload")
        _cache_write(blocked)
        return AccessCheck(AccessStatus.BLOCKED if blocked else AccessStatus.ALLOWED)
    except Exception:
        remembered, fresh = _cache_read()
        if remembered is not None and fresh:
            return AccessCheck(
                AccessStatus.BLOCKED if remembered else AccessStatus.ALLOWED,
                from_cache=True,
            )
        return AccessCheck(AccessStatus.BLOCKED)


#: What the client reads. Never says "blocked", "suspended" or anything about
#: billing — see the module docstring and `acesso.rules.ts` in the control plane.
BLOCKED_MESSAGE = (
    "Nao consegui processar sua solicitacao agora. "
    "Entre em contato com o suporte da CyriusX."
)
