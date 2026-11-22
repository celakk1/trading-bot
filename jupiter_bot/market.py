import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

SOL = "So11111111111111111111111111111111111111112"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
PROBE_LAMPORTS = 100_000_000
MAX_INT = 2**63 - 1
ORDER_URL = "https://api.jup.ag/swap/v2/order"


def nowMs():
    return time.time_ns() // 1_000_000


class QuoteError(ValueError):
    pass


@dataclass(frozen=True)
class Quote:
    inputMint: str
    outputMint: str
    inAmount: int
    outAmount: int
    minOut: int
    slippageBps: int
    impactBps: int
    feeBps: int
    observedMs: int
    expiresMs: int | None = None

    def isFresh(self, atMs, maxAgeMs):
        return (0 <= atMs - self.observedMs <= maxAgeMs
                and (self.expiresMs is None or atMs < self.expiresMs))


def orderInt(value, minimum=0):
    if type(value) is str and value.isascii() and value.isdecimal() and len(value) <= 19:
        value = int(value)
    if type(value) is not int or not minimum <= value <= MAX_INT:
        raise ValueError("invalid order integer")
    return value


def parseOrder(data, inputMint, outputMint, amount, observedMs):
    try:
        if (data.get("error") or data.get("errorCode") or data.get("errorMessage")
                or data.get("transaction") or data.get("taker")
                or data["inputMint"] != inputMint or data["outputMint"] != outputMint
                or data["swapMode"] != "ExactIn" or orderInt(data["inAmount"], 1) != amount):
            raise ValueError("unexpected order")
        out = orderInt(data["outAmount"], 1)
        minOut = orderInt(data["otherAmountThreshold"], 1)
        slippage = orderInt(data["slippageBps"])
        fee = orderInt(data["feeBps"])
        impactValue = data["priceImpact"]
        if type(impactValue) not in (int, float, Decimal):
            raise ValueError("invalid impact")
        impact = abs(Decimal(str(impactValue))) * 100
        if minOut > out or max(fee, slippage) > 10_000 or not 0 <= impact <= 10_000:
            raise ValueError("invalid order costs")
        expiresMs = None
        if data.get("expireAt"):
            expiry = datetime.fromisoformat(data["expireAt"])
            if expiry.tzinfo is None:
                raise ValueError("expiry needs a timezone")
            expiresMs = int(expiry.timestamp() * 1000)
        return Quote(inputMint, outputMint, amount, out, minOut, slippage,
                     int(impact.to_integral_value(rounding=ROUND_CEILING)),
                     fee, observedMs, expiresMs)
    except (AttributeError, KeyError, ValueError, TypeError, InvalidOperation, OverflowError, OSError):
        raise QuoteError("invalid or unavailable Jupiter quote") from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Jupiter:
    def __init__(self):
        self.opener = build_opener(NoRedirect())
        self.key = os.environ.get("JUPITER_API_KEY", "").strip()
        self.nextRequest = 0.0
        self.cooldown = 0.0

    def quote(self, inputMint, outputMint, amount):
        if time.monotonic() < self.cooldown:
            raise QuoteError("Jupiter rate-limit cooldown")
        time.sleep(max(0, self.nextRequest - time.monotonic()))
        self.nextRequest = time.monotonic() + 2.1
        query = urlencode({"inputMint": inputMint, "outputMint": outputMint, "amount": amount})
        request = Request(f"{ORDER_URL}?{query}", headers={"Accept": "application/json"})
        if self.key:
            request.add_header("x-api-key", self.key)
        observedMs = nowMs()
        try:
            with self.opener.open(request, timeout=5) as response:
                body = response.read(1_048_577)
                self.applyCooldown(response.headers)
            if len(body) > 1_048_576:
                raise QuoteError("oversized Jupiter quote")
            data = json.loads(body, parse_float=Decimal)
        except HTTPError as error:
            with error:
                if error.code == 429:
                    self.applyCooldown(error.headers, limited=True)
            raise QuoteError(f"Jupiter HTTP {error.code}") from None
        except (URLError, OSError, HTTPException, UnicodeError, json.JSONDecodeError, RecursionError):
            raise QuoteError("Jupiter request failed or returned invalid JSON") from None
        return parseOrder(data, inputMint, outputMint, amount, observedMs)

    def applyCooldown(self, headers, limited=False):
        if limited or headers.get("x-ratelimit-remaining") == "0":
            try:
                reset = float(headers.get("x-ratelimit-reset", "nan"))
                delay = max(2.1, reset - time.time()) if math.isfinite(reset) else 60
            except ValueError:
                delay = 60
            self.cooldown = time.monotonic() + delay
