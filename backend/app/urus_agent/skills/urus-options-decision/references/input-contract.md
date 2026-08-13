# Input contract

Accept one `urus.stage4b_decision_packet.v1` JSON object. Prefer an `options` projection containing only the requested symbol.

Use these paths:

- `quality`: warnings and blocking errors.
- `source`: dataset identity, capture time, and source hash.
- `observations.pre_market.options` and `observations.pre_close.options`: option snapshots and model assumptions.
- `observations.<phase>.options.symbols[].overview`: call/put volume and open interest, IV, IV rank/percentile, and HV30.
- `observations.<phase>.options.symbols[].expirations[]`: DTE, max pain, expected move, DEX/GEX totals, walls, gamma zones, strike sign changes, and Spot Gamma Profile summary.
- `paired_changes.options[]`: pre-market to pre-close changes by symbol and expiration.
- `observations.<phase>.instruments[]`, `observations.<phase>.market`, and `events.records[]`: confirming price, technical, regime, and event evidence.
- `execution_ready` and `execution_blockers`: whether exact trade economics can be produced.

The packet intentionally omits raw option rows and Spot Gamma Profile points. Do not ask the model to reconstruct them. Contract-level bid, ask, premium, volume, and open interest are not guaranteed to be present.
