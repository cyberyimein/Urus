# Output contract

Return one JSON object with all fields present:

```json
{
  "schema_version": "urus.options_decision.v1",
  "symbol": "QQQ",
  "as_of": "ISO-8601 timestamp or null",
  "status": "decision|no_trade|insufficient_data",
  "gamma_regime": "positive_gamma|negative_gamma|near_flip|mixed|unknown",
  "thesis": "concise evidence-based statement",
  "horizon": {"expiration": "YYYY-MM-DD or null", "days_to_expiry": 0},
  "structure": {
    "kind": "call_vertical|put_vertical|long_call_butterfly|long_put_butterfly|iron_condor|calendar|none",
    "execution_ready": false,
    "legs": [
      {"side": "buy|sell", "option_type": "call|put", "strike": 0, "expiration": "YYYY-MM-DD", "quantity": 1, "premium": null}
    ],
    "net_debit_or_credit": null,
    "max_profit": null,
    "max_loss": null,
    "breakevens": []
  },
  "scenario_anchors": {
    "spot": null,
    "expected_move": null,
    "max_pain": null,
    "primary_gamma_flip": null,
    "call_wall": null,
    "put_wall": null
  },
  "confidence": 0.0,
  "evidence": [{"path": "packet.path", "observation": "what the value supports"}],
  "uncertainties": [],
  "invalidation_conditions": [],
  "disclaimer": "Research output only; no order was placed."
}
```

`confidence` must be between 0 and 1. Use `kind=none` and an empty `legs` array for `no_trade` or `insufficient_data`. Do not add fields outside this contract.
