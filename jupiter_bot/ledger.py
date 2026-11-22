import json
import sqlite3

from .market import MAX_INT
from .trading import State


class Ledger:
    def __init__(self, path, identity, allocationUsdc):
        self.initial = State(allocationUsdc, 0, None, allocationUsdc, allocationUsdc, 0)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        try:
            with self.db:
                self.db.execute("BEGIN IMMEDIATE")
                tables = self.db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                if not tables:
                    self.db.execute("CREATE TABLE run (identity TEXT NOT NULL)")
                    self.db.execute("CREATE TABLE decisions (tickMs INTEGER PRIMARY KEY, event TEXT NOT NULL)")
                    self.db.execute("INSERT INTO run VALUES (?)", (identity,))
                if self.db.execute("SELECT identity FROM run").fetchall() != [(identity,)]:
                    raise ValueError("ledger belongs to a different mode, config, strategy, or fixture")
                self.db.execute("SELECT event FROM decisions LIMIT 0")
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def snapshot(self, tickMs, cfg):
        rows = self.db.execute("SELECT tickMs, event FROM decisions ORDER BY tickMs DESC LIMIT ?",
                               (cfg.lookback,)).fetchall()
        if not rows:
            return None, self.initial, []
        head = rows[0][0]
        try:
            recent = [(previous, json.loads(event)) for previous, event in rows]
            state = State(**recent[0][1]["state"])
            if any(type(value) is not int or not 0 <= value <= MAX_INT
                   for name, value in vars(state).items() if name != "day"):
                raise ValueError("invalid ledger balances")
            if (state.day is None and state != self.initial
                    or state.day is not None and (type(state.day) is not int or state.day < 0)):
                raise ValueError("invalid ledger day")
            history = []
            newer = tickMs
            for previous, event in recent:
                if not event["usableMark"] or not 0 < newer - previous <= cfg.maxSampleGapMs:
                    break
                price = event["mark"]["outAmount"]
                if type(price) is not int or not 0 < price <= MAX_INT:
                    raise ValueError("invalid ledger mark")
                history.append(event)
                newer = previous
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("invalid ledger state") from error
        return head, state, history

    def append(self, tickMs, expectedHead, event):
        with self.db:
            result = self.db.execute(
                "INSERT INTO decisions SELECT ?, ? WHERE (SELECT max(tickMs) FROM decisions) IS ?",
                (tickMs, json.dumps(event, sort_keys=True), expectedHead),
            )
            if result.rowcount != 1:
                raise ValueError("ledger changed during decision, stop and restart with one writer")
