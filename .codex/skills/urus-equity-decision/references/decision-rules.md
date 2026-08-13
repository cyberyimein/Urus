# Decision rules

## Regime first

Use the broad ETFs when present:

- SPY for broad US equities.
- QQQ for growth and large technology.
- SMH for semiconductors.
- IGV for software.

Evaluate trend, price versus moving averages, MACD, volatility, and event risk. Match a symbol to its theme regime instead of applying only QQQ to every candidate.

## Urus trend and momentum score

Assess the available evidence without calling it strict SEPA:

- Price above rising MA20, MA50, MA100, and MA200 supports trend alignment.
- MA20 above MA50 and MA50 above MA200 supports a stronger staircase.
- Distance from 252-day high and low indicates price position.
- Positive 20-day and 60-day excess/residual returns versus QQQ support relative strength.
- MACD direction and histogram, Bollinger position/bandwidth, ATR, and realized volatility refine momentum and risk.
- Volume effort/result classifies participation and price response. Keep neutral combinations visible rather than collapsing them into a bullish or bearish label.

MA100 may be described as an Urus proxy but is not MA150. If MA150 or fundamentals are missing, set strict SEPA completeness to `partial` and list the missing fields.

## Paired confirmation

Compare price state from pre-market to pre-close. Treat cumulative session-volume differences as contextual only because the measurement windows differ. Prefer a stable close confirmation over a transient pre-market move, while retaining both observations.

## Events and risk

Flag imminent earnings, macro releases, unverified dates, missing results, and low-confidence events. Do not infer event outcomes from price action. Options expected move may describe implied risk but does not replace the event record.

## Ranking

Use `setup_ready` only when trend, relative strength, confirmation, data quality, and event risk are mutually acceptable. Use `watch` for constructive but incomplete setups, `observe` for neutral evidence, `avoid` for materially adverse evidence, and `insufficient_data` when the record cannot support a judgment.
