# Input contract

Accept one `urus.stage4b_decision_packet.v1` JSON object. Prefer the `equity` projection, which intentionally omits complete option chains.

Use these paths:

- `quality`, `source`, and observation timestamps for provenance and gating.
- `observations.<phase>.market`: QQQ primary market view, cross-asset ETF quotes, trend, technical state, and quality.
- `observations.<phase>.instruments[]`: symbol, asset type, themes, quote, technical indicators, relative strength, and warnings.
- `observations.<phase>.instruments[].technical.rsi_context`: deterministic RSI extreme-state context, continuation/reversal scores, and the exact price/momentum/volume flags used to classify it.
- `paired_changes.instruments[]`: price/change/volume deltas between pre-market and pre-close observations.
- `events.records[]`: scheduled time, status, results, confidence, and market reactions.
- `observations.<phase>.options.symbols[]`: optional context for expected move and exposure; do not make it a mandatory equity-screen field.

The packet does not currently guarantee EPS growth, revenue growth, margins, institutional ownership, MA150, or pattern geometry. Report those fields as missing where strict SEPA evaluation requires them.
