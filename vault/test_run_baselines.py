from datetime import UTC, datetime
from decimal import Decimal

from vault.run_baselines import PRICE_TICK_SCALE, SCENARIOS, proxy_quote, run_strategy
from vault.verify_hourly_bars import AVAILABILITY_DELAY, HOUR


START = datetime(2026, 7, 15, tzinfo=UTC)


def bars(prices):
    rows = []
    for index, price in enumerate(prices):
        start = START + index * HOUR
        end = start + HOUR
        rows.append((start, end, end + AVAILABILITY_DELAY,
                     0, 0, 0, None, None, None if price is None else Decimal(price)))
    return rows


def test_proxy_quote_applies_directional_cost_in_integer_units():
    price = 100 * PRICE_TICK_SCALE
    buy = proxy_quote("buy", 10_000_000, price, 0, 50)
    sell = proxy_quote("sell", 100_000_000, price, 0, 50)
    assert (buy.outAmount, buy.minOut) == (100_000_000, 99_502_487)
    assert (sell.outAmount, sell.minOut) == (10_000_000, 9_950_000)


def test_momentum_uses_scheduled_future_bars_and_counts_round_trip():
    # Fourth bar hits the 100 bps threshold. Base buys at 105 and sells at 98.
    # Stress buys at 104 and sells at 97, using its extra bar on both sides.
    rows = bars([100, 100, 100, 101, 103, 105, 104, 100, 99, 98, 97])
    end = START + 24 * HOUR
    base = run_strategy("momentum", rows, end, SCENARIOS["base"])
    stress = run_strategy("momentum", rows, end, SCENARIOS["stress"])
    assert base["fills"] == stress["fills"] == 2
    assert base["actionable_intents"] == stress["actionable_intents"] == 2
    assert base["completed_round_trips"] == stress["completed_round_trips"] == 1
    assert base["residual_sol_lamports"] == stress["residual_sol_lamports"] == 0
    assert base["terminal_equity_micro_usdc"] == 99_200_464
    assert stress["terminal_equity_micro_usdc"] == 99_062_231


def test_gap_cancels_pending_intent_and_resets_lookback():
    rows = bars([100, 100, 100, 101, None, 103, 104, 105])
    result = run_strategy("momentum", rows, START + 24 * HOUR, SCENARIOS["base"])
    assert result["actionable_intents"] == result["missed"] == 1
    assert result["fills"] == 0
    assert result["net_pnl_usdc"] == "0.000000"


def test_proxy_observed_at_window_end_is_missed():
    rows = bars([100, 100, 120, 140])
    end = rows[2][2]
    result = run_strategy("buy_and_hold", rows, end, SCENARIOS["base"])
    assert result["actionable_intents"] == result["missed"] == 1
    assert result["fills"] == 0
    assert result["terminal_equity_micro_usdc"] == 100_000_000


def test_terminal_mark_and_loss_cap_reporting_for_passive_comparator():
    rows = bars([100, 100, 100, 60])
    end = START + 24 * HOUR
    passive = run_strategy("buy_and_hold", rows, end, SCENARIOS["base"])
    cash = run_strategy("cash", rows, end, SCENARIOS["base"])
    assert passive["fills"] == 1 and passive["completed_round_trips"] == 0
    assert passive["residual_sol_lamports"] > 0
    assert passive["first_passive_loss_cap_at_utc"] == rows[-1][2].isoformat()
    assert passive["terminal_equity_micro_usdc"] < 95_000_000
    assert cash["terminal_equity_micro_usdc"] == 100_000_000
    assert cash["fill_rate_pct"] is None and cash["actionable_intents"] == 0
