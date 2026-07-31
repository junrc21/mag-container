"""Authoritative MAG credit guard shared by chat and cron runtime paths."""

from dataclasses import dataclass
from enum import Enum
import json
import os
from typing import Optional
from urllib.parse import quote
from urllib.request import Request, urlopen


class CreditStatus(Enum):
    AVAILABLE = "available"
    EXHAUSTED = "exhausted"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class CreditCheck:
    status: CreditStatus
    plan: Optional[str] = None


def check_authoritative_credits(timeout_seconds: float = 4.0) -> CreditCheck:
    """Read the tenant's total remaining balance from the MAG control plane.

    Missing configuration, malformed responses, and transport errors are reported as
    UNAVAILABLE so callers can fail closed without coupling the runtime to Postgres.
    """
    api_url = (os.getenv("MAG_API_URL") or "").rstrip("/")
    internal_key = os.getenv("MAG_INTERNAL_KEY") or os.getenv("MAG_API_INTERNAL_KEY") or ""
    tenant_slug = os.getenv("MAG_TENANT_SLUG") or ""
    if not api_url or not internal_key or not tenant_slug:
        return CreditCheck(CreditStatus.UNAVAILABLE)

    try:
        request = Request(
            f"{api_url}/internal/runtime/{quote(tenant_slug, safe='')}/credits",
            headers={"x-internal-key": internal_key},
            method="GET",
        )
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        remaining = payload.get("creditsRemaining")
        if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
            return CreditCheck(CreditStatus.UNAVAILABLE)
        status = CreditStatus.EXHAUSTED if remaining <= 0 else CreditStatus.AVAILABLE
        plan = payload.get("plan") if isinstance(payload.get("plan"), str) else None
        return CreditCheck(status, plan)
    except Exception:
        return CreditCheck(CreditStatus.UNAVAILABLE)
