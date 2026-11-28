"""Evaluate the three proposed SOL/USDC baselines on a local hourly export."""

import argparse
import json
import sys
from collections import Counter, deque
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from jupiter_bot.market import MAX_INT, PROBE_LAMPORTS, SOL, USDC, Quote
from jupiter_bot.trading import BPS, Config, Intent, State, checkRisk, refreshState
from vault.verify_hourly_bars import audit, read_export


WINDOWS = {
    "development-1": ("2026-07-15", "2026-07-29"),
    "development-2": ("2026-07-29", "2026-08-12"),
    "development-3": ("2026-08-12", "2026-08-27"),
    "holdout": ("2026-08-27", "2026-11-26"),
}
# Price ticks are 10^-8 USDC per SOL. This divisor converts ticks times
# lamports to micro-USDC.
PRICE_TICK_SCALE = 100_000_000
MICRO_USDC_DIVISOR = 100_000_000_000


@dataclass(frozen=True)
class Scenario:
    delay_bars: int
    adverse_bps: int
    fee_micro_usdc: int

    def policy(self):
        return Config(
            liveEnabled=False, allowedMints=[SOL, USDC], allocationUsdc=100_000_000,
            entryUsdc=10_000_000, perTradeUsdc=25_000_000, perAssetUsdc=30_000_000,
            totalExposureUsdc=30_000_000, dailyTurnoverUsdc=60_000_000,
            dailyLossUsdc=5_000_000, maxQuoteAgeMs=1_000, maxSlippageBps=self.adverse_bps,
            maxImpactBps=0, simAdverseBps=0, feeAllowanceUsdc=self.fee_micro_usdc,
            lookback=3, entryBps=100, exitBps=50, maxSampleGapMs=3_600_001,
            intervalSeconds=3_600,
        )


SCENARIOS = {"base": Scenario(2, 50, 20_000), "stress": Scenario(3, 100, 40_000)}


def proxy_quote(side, amount, price_ticks, at_ms, adverse_bps):
    if side == "buy":
        input_mint, output_mint = USDC, SOL
        converted = amount * MICRO_USDC_DIVISOR
        expected = converted // price_ticks
        adverse = converted * BPS // (price_ticks * (BPS + adverse_bps))
    else:
        input_mint, output_mint = SOL, USDC
        converted = amount * price_ticks
        expected = converted // MICRO_USDC_DIVISOR
        adverse = converted * (BPS - adverse_bps) // (MICRO_USDC_DIVISOR * BPS)
    if expected > MAX_INT:
        raise ValueError("VWAP proxy output exceeds the supported integer range")
    return Quote(input_mint, output_mint, amount, expected, adverse, adverse_bps, 0, 0, at_ms)


def percent(numerator, denominator):
    if not denominator:
        return None
    return f"{Decimal(numerator) * 100 / Decimal(denominator):.6f}"


def run_strategy(strategy, rows, end, scenario):
    cfg = scenario.policy()
    state = State(cfg.allocationUsdc, 0, None, cfg.allocationUsdc, cfg.allocationUsdc, 0)
    recent_prices = deque(maxlen=cfg.lookback)
    pending = None
    actionable = missed = fills = round_trips = turnover = 0
    rejections = Counter()
    fills_by_day, closing_equity = {}, {}
    peak = cfg.allocationUsdc
    max_drawdown = Fraction(0)
    first_passive_loss = None
    for index, row in enumerate(rows):
        available = row[2]
        if available >= end:
            break
        day = available.date().isoformat()
        if row[8] is None or row[5]:
            recent_prices.clear()
            if pending is not None:
                pending = None
                missed += 1
            closing_equity[day] = state.equityUsdc
            continue
        if strategy == "cash":
            closing_equity[day] = state.equityUsdc
            continue

        price_ticks = int(row[8] * PRICE_TICK_SCALE)
        at_ms = int(available.timestamp()) * 1_000
        mark = proxy_quote("sell", PROBE_LAMPORTS, price_ticks, at_ms, scenario.adverse_bps)
        if mark.outAmount <= 0:
            raise ValueError("VWAP proxy is below the supported probe precision")
        state = refreshState(state, mark, at_ms, cfg)
        if pending is not None and index == pending[1]:
            side = pending[0]
            pending = None
            fee = cfg.feeAllowanceUsdc
            budget = cfg.perTradeUsdc - fee
            if side == "sell":
                amount = min(state.solLamports, budget * MICRO_USDC_DIVISOR // price_ticks)
            elif strategy == "buy_and_hold":
                amount = min(budget, state.cashUsdc - fee, cfg.perAssetUsdc, cfg.totalExposureUsdc)
            else:
                amount = min(cfg.entryUsdc, state.cashUsdc - fee)
            quote = proxy_quote(side, max(0, amount), price_ticks, at_ms, scenario.adverse_bps)
            intent = Intent(f"{at_ms}:{side}", side, quote.inputMint, quote.outputMint, amount)
            reason, after = checkRisk(state, intent, quote, mark, at_ms, False, cfg)
            if reason:
                rejections[reason] += 1
            else:
                round_trips += int(side == "sell" and after.solLamports == 0)
                fills += 1
                fills_by_day[day] = fills_by_day.get(day, 0) + 1
                turnover += after.turnoverUsdc - state.turnoverUsdc
                state = after

        signal = None
        if pending is None and strategy == "buy_and_hold" and not (fills or rejections):
            signal = "buy"
        if pending is None and strategy == "momentum" and len(recent_prices) == cfg.lookback:
            prior = recent_prices[0]
            move = (price_ticks - prior) * BPS
            if state.solLamports == 0 and move >= prior * cfg.entryBps:
                signal = "buy"
            elif state.solLamports > 0 and move <= -prior * cfg.exitBps:
                signal = "sell"
        if signal:
            pending = (signal, index + scenario.delay_bars)
            actionable += 1
        recent_prices.append(price_ticks)

        equity = state.equityUsdc
        if (strategy == "buy_and_hold" and state.solLamports and not first_passive_loss
                and state.openingEquityUsdc - equity >= cfg.dailyLossUsdc):
            first_passive_loss = available.isoformat()
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, Fraction(peak - equity, peak))
        closing_equity[day] = equity

    if pending is not None:
        missed += 1
    rejected = sum(rejections.values())
    pnl = state.equityUsdc - cfg.allocationUsdc
    return {
        "terminal_equity_micro_usdc": state.equityUsdc,
        "net_pnl_usdc": f"{Decimal(pnl) / 1_000_000:.6f}",
        "net_return_pct": percent(pnl, cfg.allocationUsdc),
        "max_drawdown_pct": percent(max_drawdown.numerator, max_drawdown.denominator),
        "drawdown_within_5_pct": max_drawdown <= Fraction(1, 20),
        "turnover_usdc": f"{Decimal(turnover) / 1_000_000:.6f}",
        "residual_sol_lamports": state.solLamports,
        "actionable_intents": actionable,
        "fills": fills,
        "rejected": rejected,
        "missed": missed,
        "completed_round_trips": round_trips,
        "fill_rate_pct": percent(fills, actionable),
        "reject_rate_pct": percent(rejected, actionable),
        "miss_rate_pct": percent(missed, actionable),
        "risk_rejections": dict(rejections),
        "fills_by_utc_day": fills_by_day,
        "closing_equity_by_utc_day_micro_usdc": closing_equity,
        "first_passive_loss_cap_at_utc": first_passive_loss,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path, help="local CSV or Parquet for one protocol window")
    parser.add_argument("--window", choices=WINDOWS, required=True)
    parser.add_argument("--evidence", choices=("synthetic", "historical"), required=True)
    parser.add_argument("--revision", required=True, help="Git revision of the evaluator")
    parser.add_argument("--expected-sha256", help="canonical hash of the approved export")
    args = parser.parse_args()
    if args.evidence == "historical" and args.expected_sha256 is None:
        parser.error("historical evaluation requires --expected-sha256")
    start, end = (
        datetime.combine(date.fromisoformat(day), time.min, tzinfo=UTC) for day in WINDOWS[args.window]
    )
    try:
        indexed = read_export(args.export, start, end)
        coverage = audit(indexed, start, end, for_scoring=True)
        if args.expected_sha256 is not None and args.expected_sha256.lower() != coverage["canonical_sha256"]:
            raise ValueError("canonical dataset SHA-256 does not match")
        if coverage["passed"]:
            rows = [indexed[hour] for hour in sorted(indexed)]
            results = {
                name: {
                    strategy: run_strategy(strategy, rows, end, scenario)
                    for strategy in ("cash", "buy_and_hold", "momentum")
                }
                for name, scenario in SCENARIOS.items()
            }
        else:
            results = {}
    except (OSError, OverflowError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps({
        "protocol": "sol-usdc-baselines-v1",
        "window": args.window,
        "revision": args.revision,
        "evidence": args.evidence,
        "coverage": coverage,
        "results": results,
    }, indent=2))
    return 0 if results else 1


if __name__ == "__main__":
    sys.exit(main())
