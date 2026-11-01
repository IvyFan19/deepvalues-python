import json
import unittest

from deepvalues import Client, NotFound, PaymentRequired, QuotaExceeded, mint_key
from deepvalues.client import Response


class FakeTransport:
    """Scripted answers keyed by method + path; records every request."""

    def __init__(self):
        self.calls = []
        self.answers = {}

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        path = url.split("/api/v1", 1)[1].split("?")[0]
        status, payload, hdrs = self.answers.get((method, path), (200, {"ok": True}, {}))
        return Response(status, {"Content-Type": "application/json", **hdrs}, json.dumps(payload).encode())


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.t = FakeTransport()
        self.dv = Client(transport=self.t)

    def test_keyless_call_builds_the_url_and_sends_no_auth(self):
        self.t.answers[("GET", "/smart-money/holdings")] = (200, {"holders": []}, {"X-RateLimit-Remaining": "24"})
        out = self.dv.holdings("AAPL", quarter_end="2026-06-30")
        self.assertEqual(out, {"holders": []})
        method, url, headers, _ = self.t.calls[0]
        self.assertEqual(method, "GET")
        self.assertIn("/smart-money/holdings?ticker=AAPL&quarter_end=2026-06-30", url)
        self.assertNotIn("Authorization", headers)
        self.assertEqual(self.dv.remaining, 24)

    def test_keyed_call_sends_the_bearer(self):
        dv = Client(api_key="dv_sk_live_x", transport=self.t)
        dv.intrinsic_value("BRK.B")
        self.assertEqual(self.t.calls[0][2]["Authorization"], "Bearer dv_sk_live_x")
        self.assertIn("/intrinsic-value/BRK.B", self.t.calls[0][1])

    def test_none_params_are_dropped(self):
        self.dv.fundamentals("MSFT")
        self.assertTrue(self.t.calls[0][1].endswith("/fundamentals/MSFT?period=annual"))

    def test_429_carries_remedies_and_retry_after(self):
        self.t.answers[("GET", "/gurus")] = (
            429,
            {"type": "https://app.deepvalues.ai/developers/errors/anonymous-quota-exceeded", "title": "Keyless allowance used up",
             "status": 429, "retry_after_seconds": 3600, "remedies": [{"kind": "mint_key"}]},
            {},
        )
        with self.assertRaises(QuotaExceeded) as cm:
            self.dv.gurus()
        self.assertEqual(cm.exception.retry_after, 3600)
        self.assertEqual(cm.exception.remedies[0]["kind"], "mint_key")

    def test_402_carries_the_top_up_and_price(self):
        self.t.answers[("GET", "/research/AAPL")] = (
            402,
            {"title": "Payment required", "status": 402, "price_credits": 3, "topUp": {"checkoutUrl": "https://checkout.stripe.com/x"}},
            {},
        )
        with self.assertRaises(PaymentRequired) as cm:
            Client(api_key="dv_sk_live_x", transport=self.t).research("AAPL")
        self.assertEqual(cm.exception.price_credits, 3.0)
        self.assertEqual(cm.exception.top_up["checkoutUrl"], "https://checkout.stripe.com/x")
        self.assertIsNone(cm.exception.x402_quote)

    def test_404_is_not_found(self):
        self.t.answers[("GET", "/companies/ZZZZ/overview")] = (404, {"title": "Unknown ticker", "status": 404}, {})
        with self.assertRaises(NotFound):
            self.dv.overview("ZZZZ")

    def test_mint_key_posts_the_email_and_returns_the_key(self):
        self.t.answers[("POST", "/keys")] = (201, {"key": "dv_sk_live_abc", "environment": "live"}, {})
        key = mint_key("agent@example.com", transport=self.t)
        self.assertEqual(key, "dv_sk_live_abc")
        self.assertEqual(json.loads(self.t.calls[0][3]), {"email": "agent@example.com"})

    def test_checkout_posts_the_pack(self):
        self.t.answers[("POST", "/credits/checkout")] = (200, {"checkoutUrl": "https://checkout.stripe.com/y"}, {})
        out = Client(api_key="k", transport=self.t).checkout("5000")
        self.assertEqual(out["checkoutUrl"], "https://checkout.stripe.com/y")
        self.assertEqual(json.loads(self.t.calls[0][3]), {"pack": "5000"})


if __name__ == "__main__":
    unittest.main()
