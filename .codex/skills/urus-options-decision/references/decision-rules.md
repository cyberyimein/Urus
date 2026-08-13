# Decision rules

## Exposure interpretation

- Positive modeled net GEX generally supports a more damped, mean-reverting scenario; negative modeled net GEX generally supports a more reactive scenario. Treat this as a dealer-position model assumption, not an observed dealer book.
- `primary_gamma_flip` is scenario-based. Compare it with spot and report their signed and percentage distance.
- Walls identify concentrated modeled exposure. Require the wall's expiration and exposure type in every claim.
- Use expected move as a volatility-implied range anchor. Do not state that price must remain inside it.
- Max pain may help describe expiry positioning but must never be the sole thesis.

## Structure selection

- Use a long call or put vertical for bounded directional exposure.
- Use a long butterfly when a specific expiry target zone has several confirming anchors and limited movement is expected near the body strike.
- Use an iron condor only for a range thesis with positive/stable gamma evidence and defined wings.
- Use a calendar only when separate near/far expirations and IV/DTE evidence exist. Otherwise return `no_trade` or another structure.
- Never return naked short calls or puts.

## Payoff integrity

For expiry price `S`, call payoff is `max(S-K, 0)` and put payoff is `max(K-S, 0)`. A leg's profit equals signed payoff minus signed premium. Sum all leg profits for the structure.

Only compute breakeven, max profit, max loss, debit, or credit when all leg premiums and contract multipliers are supplied. Otherwise emit null values, explain the missing quote evidence, and keep the proposal at template level.

## Confidence

Reduce confidence for stale timestamps, unavailable symbols, model warnings, low usable-contract counts, unstable flips, disagreement across expirations, or event risk. A plausible narrative without supporting packet paths is not evidence.
