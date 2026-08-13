---
name: urus-options-decision
description: Analyze an Urus Stage 4B decision packet with option exposure data (DEX, GEX, gamma flip, walls, max pain, expected move, IV, and DTE) and propose bounded-risk option structure templates. Use for option-market interpretation, butterfly/vertical/condor/calendar selection, or strict JSON option research decisions. Do not use to place orders or invent unavailable premiums.
---

# Urus Options Decision

Convert aggregated Urus option evidence into an auditable, bounded-risk research decision. Treat every output as analysis, never as permission to trade.

## Required references

Read these files before producing a decision:

1. `references/input-contract.md`
2. `references/decision-rules.md`
3. `references/output-contract.md`

## Workflow

1. Confirm `schema_version` is `urus.stage4b_decision_packet.v1`.
2. Reject the decision as `insufficient_data` when `quality.blocking_errors` is non-empty, the target symbol is absent, or both option observations are unavailable.
3. Compare pre-market and pre-close observations. Identify changed evidence; do not merely summarize the latest snapshot.
4. Select expirations consistent with the requested horizon. State the DTE and never mix walls or exposure totals across expirations.
5. Classify the environment as `positive_gamma`, `negative_gamma`, `near_flip`, `mixed`, or `unknown`. Support it with current spot net GEX, gamma flip distance, zones, and walls.
6. Form a directional or range thesis only when price/technical/event evidence agrees sufficiently. Otherwise choose `no_trade`.
7. Select only bounded-risk structures. Use walls and expected move as scenario anchors, not guaranteed support, resistance, or price targets.
8. If contract bid/ask and leg premiums are missing, return a `structure_template` and set `execution_ready=false`. Never calculate exact debit, credit, max profit, or max loss from missing premiums.
9. Return exactly one JSON object matching `references/output-contract.md`, with no Markdown or extra text.

## Guardrails

- Do not place orders, recommend naked short options, or claim execution readiness without live contract quotes.
- Keep modeled put GEX sign assumptions, rates, dividends, IV quality, and quote timestamps visible in `uncertainties`.
- Distinguish strike-level GEX sign changes from the scenario-based Spot Gamma Flip.
- Treat max pain as descriptive open-interest structure, not a forecast.
- Penalize stale, mock, partial, or internally inconsistent evidence.
- Prefer `no_trade` over filling missing facts with general market knowledge.
