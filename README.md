# deepvalues

Zero-dependency Python client for the [DeepValues API](https://app.deepvalues.ai/developers):
what Buffett and 80+ tracked investors hold (13F), intrinsic value, fundamentals, SEC
filings, value-investor sentiment, the Buffett Indicator, and a drop-in AI grounding block
for any US public company. Keyless trial, no account, pay per call.

```bash
pip install deepvalues
```

```python
from deepvalues import Client, QuotaExceeded, mint_key

dv = Client()                                  # keyless: 25 calls a day per IP
iv = dv.intrinsic_value("AAPL")                # DCF, owner earnings, Damodaran
holders = dv.holdings("AAPL")                  # which gurus own it
moves = dv.guru_moves(slugs="warren-buffett", limit=5)

try:
    ctx = dv.ai_context("NVDA")                # everything about one company, for a system prompt
except QuotaExceeded as e:                     # the allowance is spent: the server says what to do
    print(e.remedies)

dv = Client(api_key=mint_key("you@example.com"))   # a live key, 1 starter credit, no account
print(dv.me())                                     # balance in micro-credits, quota, environment
```

Errors are typed so an agent can act without parsing text:

| Exception | When | Carries |
|---|---|---|
| `QuotaExceeded` (429) | daily allowance spent | `remedies`, `retry_after` |
| `PaymentRequired` (402) | balance short | `price_credits`, `top_up["checkoutUrl"]`, `x402_quote` |
| `NotFound` (404) | unknown ticker or guru | the problem body |
| `ApiError` | anything else non-2xx | `status`, `problem`, `headers` |

Prices are decimals: most reads cost 0.001 or 0.005 credits (1 credit = $1); `research()`
costs 3 and needs a key. Full index for agents: https://app.deepvalues.ai/llms.txt.

## Develop

```bash
python -m unittest discover -s tests
```

MIT. Data is for research; nothing here is investment advice.
