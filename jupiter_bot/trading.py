import json
from dataclasses import dataclass, replace

from .market import MAX_INT, PROBE_LAMPORTS, SOL, USDC

STRATEGY_VERSION = "momentum-v1"
POLICY_VERSION = "paper-risk-v1"
BPS = 10_000
DAY_MS = 86_400_000


@dataclass(frozen=True)
class Config:
    liveEnabled: bool
    allowedMints: list[str]
    allocationUsdc: int
    entryUsdc: int
    perTradeUsdc: int
    perAssetUsdc: int
    totalExposureUsdc: int
    dailyTurnoverUsdc: int
    dailyLossUsdc: int
    maxQuoteAgeMs: int
    maxSlippageBps: int
    maxImpactBps: int
    simAdverseBps: int
    feeAllowanceUsdc: int
    lookback: int
    entryBps: int
    exitBps: int
    maxSampleGapMs: int
    intervalSeconds: int

    @classmethod
    def load(cls, path):
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        try:
            cfg = cls(**data)
        except TypeError:
            raise ValueError("config must contain exactly the settings in config.example.json") from None
        if cfg.liveEnabled is not False or (cfg.allowedMints != [SOL, USDC]
                                           and cfg.allowedMints != [USDC, SOL]):
            raise ValueError("only paper mode and the SOL/USDC mint pair are supported")
        for name, value in data.items():
            if name not in ("liveEnabled", "allowedMints"):
                if type(value) is not int or not 0 <= value <= MAX_INT:
                    raise ValueError(f"{name} must be a nonnegative integer")
        if not (0 < cfg.entryUsdc <= cfg.perTradeUsdc <= cfg.allocationUsdc
                and cfg.entryUsdc + cfg.feeAllowanceUsdc <= cfg.perTradeUsdc
                and 0 < cfg.perAssetUsdc <= cfg.allocationUsdc
                and 0 < cfg.totalExposureUsdc <= cfg.allocationUsdc
                and cfg.perTradeUsdc <= cfg.dailyTurnoverUsdc
                and 0 < cfg.dailyLossUsdc <= cfg.allocationUsdc
                and 0 < cfg.maxQuoteAgeMs <= cfg.maxSampleGapMs
                and 3 <= cfg.intervalSeconds <= 86400
                and cfg.intervalSeconds * 1000 < cfg.maxSampleGapMs
                and 1 <= cfg.lookback <= 1000
                and 0 < cfg.entryBps <= BPS and 0 < cfg.exitBps <= BPS
                and 0 <= cfg.simAdverseBps <= cfg.maxSlippageBps <= BPS
                and cfg.maxImpactBps <= BPS):
            raise ValueError("invalid or inconsistent paper limits")
        return cfg


@dataclass(frozen=True)
class State:
    cashUsdc: int
    solLamports: int
    day: int | None
    openingEquityUsdc: int
    equityUsdc: int
    turnoverUsdc: int


@dataclass(frozen=True)
class Intent:
    id: str
    side: str
    inputMint: str
    outputMint: str
    amount: int


def markedUsdc(lamports, markOut):
    return lamports * markOut // PROBE_LAMPORTS


def simulatedOut(quote, cfg):
    return min(quote.minOut, quote.outAmount * (BPS - cfg.simAdverseBps) // BPS)


def equityUsdc(state, mark, cfg):
    position = markedUsdc(state.solLamports, simulatedOut(mark, cfg))
    return state.cashUsdc + max(0, position - cfg.feeAllowanceUsdc)


def refreshState(state, mark, atMs, cfg):
    day = atMs // DAY_MS
    if state.day is not None and day < state.day:
        raise ValueError("clock precedes the ledger day")
    if state.day != day:
        state = replace(state, day=day, openingEquityUsdc=state.equityUsdc, turnoverUsdc=0)
    equity = equityUsdc(state, mark, cfg)
    if equity > MAX_INT:
        raise ValueError("marked equity out of range")
    return replace(state, equityUsdc=equity)


def quoteRejection(quote, atMs, cfg):
    if quote is None:
        return "missing_quote"
    if not quote.isFresh(atMs, cfg.maxQuoteAgeMs):
        return "stale_quote"
    haircut = (quote.outAmount - quote.minOut) * BPS
    # allow one output unit for rounding the minimum
    if (quote.slippageBps > cfg.maxSlippageBps
            or haircut > quote.outAmount * cfg.maxSlippageBps + BPS):
        return "slippage_cap"
    if quote.impactBps > cfg.maxImpactBps:
        return "price_impact_cap"
    return None


def momentum(tickMs, mark, history, state, cfg):
    if len(history) < cfg.lookback:
        return None
    previous = history[-1]["mark"]["outAmount"]
    move = (mark.outAmount - previous) * BPS
    if state.solLamports == 0 and move >= previous * cfg.entryBps:
        return Intent(f"{tickMs}:buy", "buy", USDC, SOL, cfg.entryUsdc)
    if state.solLamports and move <= -previous * cfg.exitBps:
        budget = cfg.perTradeUsdc - cfg.feeAllowanceUsdc
        amount = min(state.solLamports, budget * PROBE_LAMPORTS // mark.outAmount)
        if amount:
            return Intent(f"{tickMs}:sell", "sell", SOL, USDC, amount)
    return None


def simulate(state, intent, quote, cfg):
    fee = cfg.feeAllowanceUsdc
    output = simulatedOut(quote, cfg)
    if intent.side == "buy":
        return replace(state, cashUsdc=state.cashUsdc - intent.amount - fee,
                       solLamports=state.solLamports + output)
    return replace(state, cashUsdc=state.cashUsdc + output - fee,
                   solLamports=state.solLamports - intent.amount)


def checkRisk(state, intent, quote, mark, atMs, paused, cfg):
    if paused:
        return "paused", state
    if type(intent.amount) is not int or intent.amount <= 0:
        return "invalid_amount", state
    pair = (intent.inputMint, intent.outputMint)
    if ((intent.side, *pair) not in (("buy", USDC, SOL), ("sell", SOL, USDC))
            or any(mint not in cfg.allowedMints for mint in pair)):
        return "mint_allowlist", state
    reason = quoteRejection(mark, atMs, cfg) or quoteRejection(quote, atMs, cfg)
    if reason:
        return reason, state
    if (quote.inputMint, quote.outputMint, quote.inAmount) != (*pair, intent.amount):
        return "quote_mismatch", state
    buying = intent.side == "buy"
    notional = intent.amount if buying else max(quote.outAmount, markedUsdc(intent.amount, mark.outAmount))
    turnover = notional + cfg.feeAllowanceUsdc
    if turnover > cfg.perTradeUsdc:
        return "trade_cap", state
    if state.turnoverUsdc + turnover > cfg.dailyTurnoverUsdc:
        return "daily_turnover_cap", state
    output = simulatedOut(quote, cfg)
    if output < 1:
        return "insufficient_output", state
    if ((buying and turnover > state.cashUsdc)
            or (not buying and (intent.amount > state.solLamports
                                or output <= cfg.feeAllowanceUsdc))):
        return "insufficient_balance", state
    after = simulate(state, intent, quote, cfg)
    after = replace(after, equityUsdc=equityUsdc(after, mark, cfg),
                    turnoverUsdc=state.turnoverUsdc + turnover)
    if max(after.cashUsdc, after.solLamports, after.equityUsdc) > MAX_INT:
        return "balance_out_of_range", state
    if buying:
        exposure = markedUsdc(after.solLamports, mark.outAmount)
        if exposure > cfg.perAssetUsdc:
            return "per_asset_cap", state
        if exposure > min(cfg.totalExposureUsdc, cfg.allocationUsdc):
            return "total_exposure_cap", state
        if state.openingEquityUsdc - min(state.equityUsdc, after.equityUsdc) >= cfg.dailyLossUsdc:
            return "daily_loss_cap", state
    return None, after
