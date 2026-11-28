import json
import socket
import sqlite3
import sys
from contextlib import closing
from dataclasses import replace
from http.client import HTTPMessage
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest

from jupiter_bot import __main__ as cli
from jupiter_bot.market import PROBE_LAMPORTS, SOL, USDC, Jupiter, QuoteError, parseOrder
from jupiter_bot.trading import DAY_MS, Config, Intent, State, checkRisk, refreshState


@pytest.fixture(autouse=True)
def blockNetwork(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("tests must run offline")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.delenv("JUPITER_API_KEY", raising=False)


@pytest.fixture
def frames():
    return cli.fixtureTicks((cli.ROOT / "tests/fixtures/momentum.json").read_bytes())


@pytest.fixture
def cfg():
    return Config.load(cli.ROOT / "config.example.json")


def invoke(monkeypatch, capsys, *args):
    monkeypatch.setattr(sys, "argv", ["jupiter_bot", *map(str, args)])
    assert cli.main() == 0
    return [json.loads(line) for line in capsys.readouterr().out.splitlines()]


def readLedger(path):
    with closing(sqlite3.connect(path)) as db:
        return [json.loads(event) for (event,) in
                db.execute("SELECT event FROM decisions ORDER BY tickMs")]


def testReplayRepeatableAndDuplicateAcrossRestart(monkeypatch, capsys, tmp_path):
    firstDb = tmp_path / "first.sqlite"
    secondDb = tmp_path / "second.sqlite"
    first = invoke(monkeypatch, capsys, "replay", "--db", firstDb)
    assert first == invoke(monkeypatch, capsys, "replay", "--db", secondDb)
    assert first == readLedger(firstDb) == readLedger(secondDb)
    fills = [event for event in first if event["status"] == "simulated_fill"]
    assert [event["intent"]["side"] for event in fills] == ["buy", "sell"]
    assert fills[0]["state"]["solLamports"] == 95_520_000
    assert first[-1]["state"] == {
        "cashUsdc": 99_369_197, "solLamports": 0, "day": 20454,
        "openingEquityUsdc": 100_000_000, "equityUsdc": 99_369_197, "turnoverUsdc": 19_496_480,
    }
    retry = invoke(monkeypatch, capsys, "replay", "--db", firstDb)
    assert all(event["status"] == "duplicate_tick" for event in retry)
    assert first == readLedger(firstDb)


@pytest.mark.parametrize(("overrides", "expected"), [
    ({"perTradeUsdc": 9_000_000}, "trade_cap"),
    ({"perAssetUsdc": 9_000_000}, "per_asset_cap"),
    ({"totalExposureUsdc": 9_000_000}, "total_exposure_cap"),
    ({"dailyTurnoverUsdc": 9_000_000}, "daily_turnover_cap"),
    ({"dailyLossUsdc": 100_000}, "daily_loss_cap"),
])
def testRiskRejectsOverBudget(frames, cfg, overrides, expected):
    frame = frames[3]
    atMs = frame["atMs"]
    mark = cli.fixtureQuote(frame, SOL, USDC, PROBE_LAMPORTS)
    order = cli.fixtureQuote(frame, USDC, SOL, 10_000_000)
    state = State(100_000_000, 0, atMs // DAY_MS, 100_000_000, 100_000_000, 0)
    intent = Intent("test:buy", "buy", USDC, SOL, 10_000_000)
    assert checkRisk(state, intent, order, mark, atMs, False, replace(cfg, **overrides)) == (expected, state)


def testStaleMarkRejectionRecordsNoFill(monkeypatch, capsys, tmp_path, frames):
    frames[3]["mark"]["expireAt"] = "2026-01-01T00:02:59Z"
    fixture = tmp_path / "stale.json"
    fixture.write_text(json.dumps({"kind": "synthetic", "ticks": frames}), encoding="utf-8")
    db = tmp_path / "stale.sqlite"
    events = invoke(monkeypatch, capsys, "replay", "--fixture", fixture, "--db", db)
    assert events[3]["reason"] == "stale_quote"
    assert events[3]["usableMark"] is False
    assert all(event["fill"] is None for event in readLedger(db))
    assert events[-1]["state"]["equityUsdc"] == 100_000_000


def testMissingMarkPreservesPositionAndBreaksLookback(monkeypatch, capsys, tmp_path, frames):
    del frames[4]["mark"]
    fixture = tmp_path / "missing.json"
    fixture.write_text(json.dumps({"kind": "synthetic", "ticks": frames}), encoding="utf-8")
    db = tmp_path / "missing.sqlite"
    events = invoke(monkeypatch, capsys, "replay", "--fixture", fixture, "--db", db)

    buy, missing, recovered = readLedger(db)[3:]
    assert events[3:] == [buy, missing, recovered]
    assert buy["status"] == "simulated_fill" and buy["intent"]["side"] == "buy"
    assert missing["status"] == "rejected" and missing["reason"] == "missing_quote"
    assert missing["usableMark"] is False
    assert missing["intent"] is None and missing["fill"] is None
    assert missing["state"] == buy["state"]
    assert recovered["status"] == "hold" and recovered["usableMark"] is True
    assert recovered["intent"] is None and recovered["fill"] is None
    assert recovered["state"]["cashUsdc"] == buy["state"]["cashUsdc"]
    assert recovered["state"]["solLamports"] == buy["state"]["solLamports"]
    assert recovered["state"]["equityUsdc"] != missing["state"]["equityUsdc"]


def testPauseKeepsMarkingAndSkipsSettlement(monkeypatch, capsys, tmp_path):
    (tmp_path / "PAUSE").touch()
    events = invoke(monkeypatch, capsys, "replay", "--db", tmp_path / "paused.sqlite")
    assert events[3]["intent"]["side"] == "buy"
    assert events[3]["status"] == "rejected" and events[3]["reason"] == "paused"
    assert events[3]["usableMark"] is True and events[3]["order"] is None
    assert all(event["fill"] is None for event in events)


def testUtcResetUsesLastValuationAndRetainsOvernightLoss(cfg, frames):
    frame = frames[-1]
    mark = cli.fixtureQuote(frame, SOL, USDC, PROBE_LAMPORTS)
    day = frame["atMs"] // DAY_MS
    before = State(80_000_000, 100_000_000, day, 100_000_000, 95_000_000, 10_000_000)
    after = refreshState(before, mark, (day + 1) * DAY_MS, cfg)
    assert (after.day, after.openingEquityUsdc, after.equityUsdc, after.turnoverUsdc) == (
        day + 1, 95_000_000, 89_830_500, 0,
    )


def testPaperResumesAfterBuyWithoutDuplicateSettlement(monkeypatch, capsys, tmp_path, frames):
    position = 0
    requests = []

    class FakeJupiter:
        def quote(self, *request):
            requests.append(position)
            return cli.fixtureQuote(frames[position], *request)

    def nextFrame(seconds):
        nonlocal position
        assert seconds == 60
        position += 1

    monkeypatch.setattr(cli, "Jupiter", FakeJupiter)
    monkeypatch.setattr(cli, "nowMs", lambda: frames[position]["atMs"])
    monkeypatch.setattr(cli.time, "sleep", nextFrame)
    db = tmp_path / "paper.sqlite"
    firstRun = invoke(monkeypatch, capsys, "paper", "--db", db, "--ticks", 4)
    assert firstRun[-1]["status"] == "simulated_fill" and firstRun[-1]["intent"]["side"] == "buy"
    assert firstRun == readLedger(db)

    requests.clear()
    resumed = invoke(monkeypatch, capsys, "paper", "--db", db, "--ticks", len(frames) - position)
    assert resumed[0] == {"tickMs": firstRun[-1]["tickMs"], "status": "duplicate_tick"}
    assert requests == [4, 5, 5]
    assert resumed[-1]["status"] == "simulated_fill" and resumed[-1]["intent"]["side"] == "sell"
    paper = firstRun + resumed[1:]
    replay = invoke(monkeypatch, capsys, "replay", "--db", tmp_path / "replay.sqlite")
    assert paper == readLedger(db)
    assert [{**event, "mode": "replay"} for event in paper] == replay


def testInvalidConfigRefusesLedgerCreation(monkeypatch, capsys, tmp_path):
    data = json.loads((cli.ROOT / "config.example.json").read_text(encoding="utf-8"))
    data["liveEnabled"] = True
    config = tmp_path / "config.json"
    config.write_text(json.dumps(data), encoding="utf-8")
    db = tmp_path / "paper.sqlite"
    monkeypatch.setattr(sys, "argv", ["jupiter_bot", "paper", "--db", str(db), "--config", str(config)])
    assert cli.main() == 1 and not db.exists()
    assert "only paper mode" in capsys.readouterr().err


def testOrderParserRejectsTransactionAndRoundsImpact(frames):
    data = {**frames[0]["mark"], "priceImpact": -0.1001}
    assert parseOrder(data, SOL, USDC, PROBE_LAMPORTS, 0).impactBps == 11
    data["transaction"] = "unexpected"
    with pytest.raises(QuoteError):
        parseOrder(data, SOL, USDC, PROBE_LAMPORTS, 0)


def testJupiterRequestHasNoTakerAndBacksOffOn429(monkeypatch):
    client = Jupiter()
    requests = []

    def openOrder(request, *, timeout):
        requests.append(request)
        assert timeout == 5
        raise HTTPError(request.full_url, 429, "limited", HTTPMessage(), None)

    monkeypatch.setattr(client.opener, "open", openOrder)
    with pytest.raises(QuoteError, match="HTTP 429"):
        client.quote(SOL, USDC, PROBE_LAMPORTS)
    with pytest.raises(QuoteError, match="cooldown"):
        client.quote(SOL, USDC, PROBE_LAMPORTS)
    [request] = requests
    assert request.get_method() == "GET"
    assert urlsplit(request.full_url).path == "/swap/v2/order"
    assert parse_qs(urlsplit(request.full_url).query) == {
        "inputMint": [SOL], "outputMint": [USDC], "amount": ["100000000"],
    }
    assert request.get_header("X-api-key") is None
