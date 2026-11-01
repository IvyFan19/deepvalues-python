"""
A small, dependency-free client for the DeepValues v1 API.

    from deepvalues import Client
    dv = Client()                         # keyless: 25 calls a day per IP
    dv.intrinsic_value("AAPL")
    dv = Client(api_key=mint_key("you@example.com"))   # 1 starter credit, then pay per call

Errors are typed so an agent can act on them: `QuotaExceeded` carries the server's
`remedies` list and `retry_after`; `PaymentRequired` carries `top_up` (a Stripe
checkout the caller can open) and the x402 quote when that rail is on.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Optional

BASE_URL = "https://app.deepvalues.ai/api/v1"
USER_AGENT = "deepvalues-python/0.1.0 (+https://github.com/IvyFan19/deepvalues-python)"

Transport = Callable[[str, str, Dict[str, str], Optional[bytes]], "Response"]


class Response:
    """What a transport returns: status, headers (lower-cased keys), body bytes."""

    def __init__(self, status: int, headers: Dict[str, str], body: bytes):
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None


def _urllib_transport(method: str, url: str, headers: Dict[str, str], body: Optional[bytes]) -> Response:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return Response(resp.status, dict(resp.headers.items()), resp.read())
    except urllib.error.HTTPError as err:
        return Response(err.code, dict(err.headers.items()), err.read())


class ApiError(Exception):
    """A non-2xx answer. `problem` is the RFC 7807 body when the server sent one."""

    def __init__(self, status: int, problem: Optional[dict], headers: Dict[str, str]):
        self.status = status
        self.problem = problem or {}
        self.headers = headers
        detail = self.problem.get("detail") or self.problem.get("title") or ""
        super().__init__(f"HTTP {status}: {detail}".strip())


class NotFound(ApiError):
    """Unknown ticker, guru or resource."""


class QuotaExceeded(ApiError):
    """The daily allowance is spent. `remedies` says what to do next."""

    @property
    def remedies(self) -> list:
        return list(self.problem.get("remedies") or [])

    @property
    def retry_after(self) -> Optional[int]:
        v = self.problem.get("retry_after_seconds") or self.headers.get("retry-after")
        return int(v) if v is not None else None


class PaymentRequired(ApiError):
    """The balance cannot cover the price. Act on `top_up` or the x402 quote."""

    @property
    def price_credits(self) -> Optional[float]:
        v = self.problem.get("price_credits")
        return float(v) if v is not None else None

    @property
    def top_up(self) -> Optional[dict]:
        """`{"checkoutUrl": ..., "checkoutEndpoint": ...}` for a person to pay."""
        return self.problem.get("topUp")

    @property
    def x402_quote(self) -> Optional[dict]:
        """The x402 v1 `accepts[0]` requirement when the USDC rail is on."""
        accepts = self.problem.get("accepts")
        return accepts[0] if isinstance(accepts, list) and accepts else None


def _raise_for(resp: Response) -> None:
    if 200 <= resp.status < 300:
        return
    try:
        problem = resp.json()
    except ValueError:
        problem = None
    if not isinstance(problem, dict):
        problem = {"detail": resp.body.decode("utf-8", "replace")[:200]}
    cls = {402: PaymentRequired, 404: NotFound, 429: QuotaExceeded}.get(resp.status, ApiError)
    raise cls(resp.status, problem, resp.headers)


def mint_key(email: Optional[str] = None, label: Optional[str] = None, *, base_url: str = BASE_URL, transport: Transport = _urllib_transport) -> str:
    """Mint a key with no account. An email gives a live key with 1 starter credit;
    no email gives a sandbox key (real data, never billed, small quota, no /research).
    The raw key is returned once; store it."""
    body: Dict[str, Any] = {}
    if email:
        body["email"] = email
    if label:
        body["label"] = label
    resp = transport(
        "POST",
        f"{base_url}/keys",
        {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT},
        json.dumps(body).encode("utf-8"),
    )
    _raise_for(resp)
    data = resp.json() or {}
    key = data.get("key") or data.get("apiKey") or data.get("api_key")
    if not key:
        raise ApiError(resp.status, {"detail": "no key in response", **data}, resp.headers)
    return key


class Client:
    """One HTTP client. Pass `api_key=None` to use the keyless allowance."""

    def __init__(self, api_key: Optional[str] = None, *, base_url: str = BASE_URL, transport: Transport = _urllib_transport):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self.last_headers: Dict[str, str] = {}

    # -- plumbing ---------------------------------------------------------

    def _headers(self, json_body: bool = False) -> Dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        if json_body:
            h["Content-Type"] = "application/json"
        return h

    def request(self, method: str, path: str, params: Optional[Dict[str, Any]] = None, body: Optional[dict] = None) -> Any:
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self.base_url}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        resp = self._transport(method, url, self._headers(body is not None), data)
        self.last_headers = resp.headers
        _raise_for(resp)
        return resp.json()

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params)

    @property
    def remaining(self) -> Optional[int]:
        """`X-RateLimit-Remaining` from the last call, when the server sent it."""
        v = self.last_headers.get("x-ratelimit-remaining")
        return int(v) if v is not None else None

    # -- the key ----------------------------------------------------------

    def me(self) -> dict:
        """This key: exact balance (micro-credits), remaining quota, environment."""
        return self.get("/keys/me")

    def account(self) -> dict:
        return self.get("/account")

    def pricing(self) -> dict:
        """Prices and the ways to pay (public)."""
        return self.get("/pricing")

    def checkout(self, pack: str = "1000") -> dict:
        """A Stripe Checkout URL that tops up this key. Packs: 1000, 5000, 20000 (cents)."""
        return self.request("POST", "/credits/checkout", body={"pack": pack})

    # -- grounding --------------------------------------------------------

    def ai_context(self, ticker: str, token_budget: Optional[int] = None) -> dict:
        """One paste-ready grounding block: overview, fundamentals, 13F moves, filings, sentiment, stance."""
        return self.get(f"/ai-context/{ticker}", token_budget=token_budget)

    def research(self, ticker: str, language: str = "English") -> dict:
        """The multi-analyst briefing (3 credits, keyed only, slow)."""
        return self.get(f"/research/{ticker}", language=language)

    # -- valuation --------------------------------------------------------

    def intrinsic_value(self, ticker: str) -> dict:
        return self.get(f"/intrinsic-value/{ticker}")

    def theses(self, ticker: Optional[str] = None, sentiment: Optional[str] = None, min_conviction: Optional[int] = None, limit: Optional[int] = None) -> dict:
        return self.get("/theses", ticker=ticker, sentiment=sentiment, min_conviction=min_conviction, limit=limit)

    # -- smart money ------------------------------------------------------

    def holdings(self, ticker: str, quarter_end: Optional[str] = None) -> dict:
        """Which tracked gurus hold this ticker."""
        return self.get("/smart-money/holdings", ticker=ticker, quarter_end=quarter_end)

    def changes(self, since: str, status: Optional[str] = None, filed_since: Optional[str] = None, min_position_usd: Optional[float] = None) -> dict:
        """Material moves in a window; status one of new, added, trimmed, exited."""
        return self.get("/smart-money/changes", since=since, status=status, filed_since=filed_since, min_position_usd=min_position_usd)

    def guru_moves(self, filed_since: Optional[str] = None, slugs: Optional[str] = None, limit: Optional[int] = None) -> dict:
        """Newest filed moves first, each with its verdict."""
        return self.get("/gurus/moves", filed_since=filed_since, slugs=slugs, limit=limit)

    def verdict(self, ticker: str, guru: str, quarter: Optional[str] = None) -> dict:
        return self.get(f"/verdict/{ticker}", guru=guru, quarter=quarter)

    def mirror_brief(self, ticker: str, guru: str, quarter: Optional[str] = None) -> dict:
        return self.get(f"/mirror-brief/{ticker}", guru=guru, quarter=quarter)

    def gurus(self, style: Optional[str] = None, limit: Optional[int] = None) -> dict:
        return self.get("/gurus", style=style, limit=limit)

    def guru(self, slug: str) -> dict:
        return self.get(f"/gurus/{slug}")

    def guru_portfolio(self, slug: str, as_of: Optional[str] = None) -> dict:
        return self.get(f"/gurus/{slug}/portfolio", as_of=as_of)

    # -- company data -----------------------------------------------------

    def search(self, q: str, limit: Optional[int] = None) -> dict:
        return self.get("/companies", q=q, limit=limit)

    def overview(self, ticker: str) -> dict:
        return self.get(f"/companies/{ticker}/overview")

    def fundamentals(self, ticker: str, period: str = "annual", limit: Optional[int] = None) -> dict:
        return self.get(f"/fundamentals/{ticker}", period=period, limit=limit)

    def filings(self, ticker: str, form: Optional[str] = None, limit: Optional[int] = None) -> dict:
        return self.get(f"/filings/{ticker}", form=form, limit=limit)

    def news(self, ticker: str, since: Optional[str] = None, sentiment: Optional[str] = None) -> dict:
        return self.get(f"/news/{ticker}", since=since, sentiment=sentiment)

    def sentiment(self, ticker: str, window: str = "7d", source: str = "combined") -> dict:
        return self.get(f"/sentiment/{ticker}", window=window, source=source)

    # -- market -----------------------------------------------------------

    def buffett_indicator(self, history_years: Optional[int] = None) -> dict:
        return self.get("/indicators/buffett", history_years=history_years)

    def shiller_pe(self, history_years: Optional[int] = None) -> dict:
        return self.get("/indicators/shiller-pe", history_years=history_years)
