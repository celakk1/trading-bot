# Proposed SOL/USDC baseline protocol

Protocol `sol-usdc-baselines-v1` for [issue #1](https://github.com/celakk1/trading-bot/issues/1). Shares the [dataset manifest](SOL%20USDC%20Dataset%20Manifest.md) and the eligibility rules in [[Research]]. Freeze the reviewed protocol and dataset IDs in Git before viewing strategy metrics. The study uses historical swap prints for simulated fills.

## Windows and trial budget

Use half-open UTC intervals. Never score a decision or a fill in the next interval. Each scored interval starts with 100 USDC, zero SOL, and empty momentum state. Reset balances, unfilled intents, drawdown and risk counters on every new scored interval.

| Interval | Expanding history, ending exclusively | Scoring interval, ending exclusively |
| --- | --- | --- |
| Development fold 1 | 2026-06-01 to 2026-07-15 | 2026-07-15 to 2026-07-29 |
| Development fold 2 | 2026-06-01 to 2026-07-29 | 2026-07-29 to 2026-08-12 |
| Development fold 3 | 2026-06-01 to 2026-08-12 | 2026-08-12 to 2026-08-27 |
| Final holdout | Development only, ending 2026-08-27 | 2026-08-27 to 2026-11-26 (91 days) |

No model fitting in this version. If any preparatory row uses a forward label or simulated result for a subsequent selection step, purge it whenever its full label horizon or latest possible execution reaches a scored boundary, including the holdout. Clock-based comparisons use actual modeled availability timestamps. Do not carry an open intent across a split. The final holdout starts with empty momentum history.

The shared budget is one fixed momentum rule and the two reference baselines. Base and stress simulations are paired runs of those rules. No parameter search, seed, model selection or alternative candidates in this protocol. Keep all fold results and list any later method change under a new protocol. After development is reviewed, score the final holdout once and mark it spent.

## Strategy and execution conventions

All baselines use the same 60-minute bars, market eligibility and fee scenarios. Availability is bar end plus two minutes. A bar is usable when its price is positive, volume is positive, and it has no conflicting key. A missing or unusable bar ends the momentum lookback and retains the last valuation until a usable bar arrives. Use the first full bar that starts after the decision for the VWAP fill proxy. It is the second bar after the signal bar and becomes available two hours after the decision. In stress use the third subsequent bar. A gap between the signal and execution, including at execution time, expires the intent as missed. Skipped orders can be reconsidered only through a fresh signal.

- **Cash.** Hold the starting USDC. Returns, turnover and drawdown are zero in this simulation.
- **Buy and hold.** Signal one entry at the first usable bar and retry only if its scheduled proxy is missed. Size the entry at the largest amount permitted by the common start exposure and per-trade caps. Hold to the final reporting liquidation. Report when the passive comparator would have reached the active loss cap.
- **Momentum.** Compare the latest usable VWAP with three contiguous hourly samples earlier. From cash, an increase of at least 100 bps signals a 10 USDC buy before fixed fees. With SOL, a decrease of at least 50 bps signals a full position exit up to the per-trade cap. Keep at most one outstanding intent. A threshold equality triggers. A rejected or missed intent changes neither cash nor holdings.

Use integer lamports and micro-USDC for settlement. Size each buy within the entry cap and cash after reserving fees. Round debits and fees up and received units down. Apply the starter budget values at fill time for momentum and at entry for buy and hold. Starting capital is 100 USDC. Per-trade, SOL exposure, aggregate exposure, daily turnover and daily loss caps are 25, 30, 30, 60 and 5 USDC. Include the separate fixed fee in per-trade and turnover usage. Value projected SOL at the current VWAP when testing exposure. Use the last usable marked equity as opening equity on the first usable bar of each UTC day. Risk-reducing exits remain eligible under exposure and daily-loss rules and still respect available balance, trade size and turnover.

For a nominal fill, worsen observed VWAP by 50 bps in the direction of the trade and charge 0.02 USDC separately. The 50 bps is a combined assumed execution adjustment for the historical print proxy, including unobserved adverse price and proportional cost. It is a scenario input. Avoid adding a second unmeasured proportional fee on top. The stress scenario uses 100 bps, 0.04 USDC and the additional hourly bar. Quote availability, causal delay, route fees and order-size effects still need measurement. List data-service cost separately from simulated trading costs.

Mark both SOL comparators at the conservative hypothetical sale price including the fee. Report daily buy-and-hold marks, and calculate max drawdown from the full hourly equity sequence. Use the last usable proxy within each scoring window for net liquidation valuation with the same scenario costs. Record residual SOL immediately before that adjustment and separately count strategy fills and round trips. A reporting valuation produces no additional strategy fill in those counts.

## Acceptance and reporting

Record dataset hash, protocol ID, code revision, coverage, held-out status, missing or expired intents, risk rejections, and outcomes for all three baselines. Report net USDC and percentage return, max drawdown, turnover, fills, completed round trips and realized fill, reject and miss rates per actionable intent. Also report residual exposure and whether the result survives costs and delay, and note any concentration in a few dates or regimes.

Use the draft eligibility gates in [[Research]] for teammate approval before evaluating. A candidate needs positive net P&L and an improvement over cash and buy-and-hold, at most 5% peak-to-trough drawdown and at least 30 completed round trips in both the final holdout and at least 30 days of forward paper. Stress must retain positive net P&L and drawdown at most 5%. A later model also has to beat the fixed momentum comparator. Insufficient coverage or sample size leaves the gate unevaluated.

Historical outcomes are pending an approved dataset and protocol. The initial 168-hour data slice verifies extraction and reproducibility.
