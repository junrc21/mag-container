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
    #: Saldo restante, quando conhecido. `None` em UNAVAILABLE.
    #:
    #: O gate de turno só precisa de "acabou ou não", mas a recusa por ferramenta
    #: precisa do NÚMERO: "tem 5, a imagem custa 10" é uma comparação, não um booleano.
    remaining: Optional[int] = None
    #: Preço de cada toolset, como o control plane cobra. Vem na mesma resposta de
    #: propósito — se viesse de outro lugar, o container recusaria por um preço e a
    #: fatura cobraria outro.
    toolset_costs: Optional[dict] = None


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
        custos = payload.get("toolsetCosts")
        if not isinstance(custos, dict):
            custos = None
        return CreditCheck(status, plan, remaining=int(remaining), toolset_costs=custos)
    except Exception:
        return CreditCheck(CreditStatus.UNAVAILABLE)
