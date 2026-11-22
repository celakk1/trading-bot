import argparse
import hashlib
import json
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import asdict
from pathlib import Path

from .ledger import Ledger
from .market import MAX_INT, PROBE_LAMPORTS, SOL, USDC, Jupiter, QuoteError, nowMs, parseOrder
from .trading import (
    POLICY_VERSION,
    STRATEGY_VERSION,
    Config,
    checkRisk,
    momentum,
    quoteRejection,
    refreshState,
    simulatedOut,
)

ROOT = Path(__file__).resolve().parent.parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def maybeQuote(fetchQuote, inputMint, outputMint, amount):
    try:
        return fetchQuote(inputMint, outputMint, amount), None
    except QuoteError as error:
        return None, str(error)


def tick(ledger, cfg, configId, mode, tickMs, fetchQuote, clock, pausePath):
    head, state, history = ledger.snapshot(tickMs, cfg)
    if head is not None and tickMs <= head:
        if mode == "paper" and tickMs < head:
            raise ValueError("clock precedes the ledger head")
        return {"tickMs": tickMs, "status": "duplicate_tick"}
    mark, markError = maybeQuote(fetchQuote, SOL, USDC, PROBE_LAMPORTS)
    order = orderError = intent = None
    if not quoteRejection(mark, clock(), cfg):
        intent = momentum(tickMs, mark, history, state, cfg)
        if intent and not pausePath.exists():
            order, orderError = maybeQuote(fetchQuote, intent.inputMint, intent.outputMint, intent.amount)

    atMs = clock()
    if atMs < tickMs:
        raise ValueError("clock precedes the current tick")
    reason = quoteRejection(mark, atMs, cfg)
    usableMark = reason is None
    paused = pausePath.exists()
    status = "rejected" if reason else "hold"
    if usableMark:
        state = refreshState(state, mark, atMs, cfg)
        if intent:
            reason, state = checkRisk(state, intent, order, mark, atMs, paused, cfg)
            status = "rejected" if reason else "simulated_fill"
        elif paused:
            reason = "paused"
    event = {
        "tickMs": tickMs, "evaluatedMs": atMs, "mode": mode, "status": status,
        "reason": reason, "paused": paused, "state": asdict(state),
        "configId": configId, "strategyVersion": STRATEGY_VERSION, "policyVersion": POLICY_VERSION,
        "intent": asdict(intent) if intent else None, "usableMark": usableMark,
        "mark": asdict(mark) if mark else None, "order": asdict(order) if order else None,
        "sourceError": markError or orderError, "fill": None,
    }
    if status == "simulated_fill":
        event["fill"] = {
            "atMs": atMs, "inputUnits": intent.amount, "outputUnits": simulatedOut(order, cfg),
            "feeAllowanceUsdc": cfg.feeAllowanceUsdc, "simAdverseBps": cfg.simAdverseBps,
            "method": "worse_of_quote_minimum_or_adverse_price_plus_fixed_fee",
        }
    ledger.append(tickMs, head, event)
    return event


def fixtureTicks(fixture):
    data = json.loads(fixture)
    if not isinstance(data, dict) or data.get("kind") != "synthetic":
        raise ValueError("starter replay requires a labeled synthetic fixture")
    frames = data.get("ticks")
    if not isinstance(frames, list) or not frames:
        raise ValueError("fixture requires ticks")
    previous = 0
    for frame in frames:
        if not isinstance(frame, dict):
            raise ValueError("invalid fixture tick")
        atMs = frame.get("atMs")
        if type(atMs) is not int or not previous < atMs <= MAX_INT:
            raise ValueError("fixture ticks must have strictly increasing integer timestamps")
        previous = atMs
    return frames


def fixtureQuote(frame, inputMint, outputMint, amount):
    mark = (inputMint, outputMint, amount) == (SOL, USDC, PROBE_LAMPORTS)
    data = frame.get("mark" if mark else "order")
    if not data:
        raise QuoteError("quote absent from synthetic fixture")
    return parseOrder(data, inputMint, outputMint, amount, frame["atMs"])


def printEvent(event):
    print(json.dumps(event, sort_keys=True), flush=True)


def run(args):
    if args.command == "quote":
        a, b = (SOL, USDC) if args.side == "sell" else (USDC, SOL)
        amount = args.amount or (PROBE_LAMPORTS if args.side == "sell" else 10_000_000)
        quote = Jupiter().quote(a, b, amount)
        printEvent(asdict(quote))
        return
    cfg = Config.load(args.config)
    configId = digest(json.dumps(asdict(cfg), sort_keys=True).encode())
    fixture = args.fixture.read_bytes() if args.command == "replay" else None
    frames = fixtureTicks(fixture) if fixture is not None else None
    identity = ":".join((args.command, configId, STRATEGY_VERSION, POLICY_VERSION,
                         digest(fixture) if fixture is not None else "jupiter-v2"))
    db = args.db or Path("local", f"{args.command}.sqlite")
    pausePath = db.parent / "PAUSE"
    with closing(Ledger(db, identity, cfg.allocationUsdc)) as ledger:
        if frames is not None:
            for frame in frames:
                atMs = frame["atMs"]
                event = tick(ledger, cfg, configId, args.command, atMs,
                             lambda *request: fixtureQuote(frame, *request), lambda: atMs, pausePath)
                printEvent(event)
        else:
            jupiter = Jupiter()
            remaining = args.ticks
            while remaining is None or remaining > 0:
                printEvent(tick(ledger, cfg, configId, args.command, nowMs(),
                                jupiter.quote, nowMs, pausePath))
                if remaining is not None:
                    remaining -= 1
                if remaining != 0:
                    time.sleep(cfg.intervalSeconds)


def positiveInt(value):
    number = int(value)
    if not 0 < number <= MAX_INT:
        raise argparse.ArgumentTypeError("enter a positive integer within signed 64-bit range")
    return number


def main():
    parser = argparse.ArgumentParser(description="Local Jupiter quote, replay and paper trading")
    commands = parser.add_subparsers(dest="command", required=True)
    quote = commands.add_parser("quote", help="request a walletless Jupiter order")
    quote.add_argument("--side", choices=("buy", "sell"), default="sell")
    quote.add_argument("--amount", type=positiveInt, help="input units (default 0.1 SOL or 10 USDC)")
    for name in ("replay", "paper"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=ROOT / "config.example.json")
        command.add_argument("--db", type=Path, help="local SQLite path")
        if name == "replay":
            command.add_argument("--fixture", type=Path, default=ROOT / "tests/fixtures/momentum.json")
        else:
            command.add_argument("--ticks", type=positiveInt, help="limit the number of polling cycles")
    args = parser.parse_args()
    try:
        run(args)
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
