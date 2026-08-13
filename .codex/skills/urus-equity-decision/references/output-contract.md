# Output contract

Return one JSON object with all fields present:

```json
{
  "schema_version": "urus.equity_decision.v1",
  "as_of": "ISO-8601 timestamp or null",
  "status": "decision|insufficient_data",
  "market_regime": {
    "classification": "risk_on|selective|neutral|risk_off|unknown",
    "confidence": 0.0,
    "evidence": [{"path": "packet.path", "observation": "what the value supports"}]
  },
  "rankings": [
    {
      "rank": 1,
      "symbol": "INTC",
      "themes": ["semiconductor"],
      "action": "setup_ready|watch|observe|avoid|insufficient_data",
      "strict_sepa_completeness": "complete|partial|not_evaluable",
      "score": 0.0,
      "confidence": 0.0,
      "thesis": "concise evidence-based statement",
      "evidence": [{"path": "packet.path", "observation": "what the value supports"}],
      "risks": [],
      "missing_fields": [],
      "invalidation_conditions": []
    }
  ],
  "portfolio_warnings": [],
  "disclaimer": "Research output only; no order was placed."
}
```

`score` and `confidence` must be between 0 and 1. Rank each symbol once. Do not add fields outside this contract and do not output Markdown or commentary around the JSON.
