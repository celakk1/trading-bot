# Paper execution verification, 2026-11-28

Experiment `paper-verification-2026-11-28` for [issue #2](https://github.com/celakk1/trading-bot/issues/2). The runtime code is from `d2df53acd74ff8bc9798124320bb39e83c584e4f`. The run used Windows, Python 3.12.14, `config.example.json`, and the starter `momentum-v1` strategy. The config ID logged by the bot was `a400d136bac728d816eafcfd3b4abf5717782ba26d5bf958465382bf1f1d51c7`. The fixture is `tests/fixtures/momentum.json`.

## Paper evidence from the synthetic fixture

Run these commands from the clone root with the README's virtual environment and a fresh path for the first replay. Use the same path for the second replay.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest -v -k 'RiskRejects or Pause or StaleMark or MissingMark or PaperResumes'
.\.venv\Scripts\python.exe -m jupiter_bot replay --db local/issue-2-replay-2026-11-28.sqlite
.\.venv\Scripts\python.exe -m jupiter_bot replay --db local/issue-2-replay-2026-11-28.sqlite
```

The first replay recorded four holds and two simulated fills (buy, then sell). It ended with 99,369,197 micro-USDC and zero lamports. The second replay printed six `duplicate_tick` results, and SQLite still held only the six original decisions.

The offline suite passed all 14 tests. The focused run passed nine cases.

| Case | Observed result |
| --- | --- |
| Caps | The risk gate returned `trade_cap`, `per_asset_cap`, `total_exposure_cap`, `daily_turnover_cap` and `daily_loss_cap` for the respective fixture overrides, with state preserved. |
| Pause | A usable mark and buy signal were present. The loop returned `paused` and skipped the trade quote and settlement. |
| Stale mark | The expired mark returned `stale_quote`. The ledger recorded no fills and final equity stayed at 100,000,000 micro-USDC. |
| Missing mark | After a buy, the missing mark returned `missing_quote` and preserved cash, inventory and the last valuation. The next usable mark updated valuation, and the strategy held while its lookback rebuilt. |
| Paper restart | The offline `paper` command reopened the ledger at the timestamp of its committed buy. It returned `duplicate_tick` without requesting another quote, then continued at the next timestamp and settled the sell once. Its six stored decisions matched the replay apart from the mode label. |

## Paper evidence from a bounded public quote run

Run these commands from the clone root in a PowerShell session with the API key cleared. Start with a fresh ledger and reuse it on restart. Jupiter's `/order` endpoint returns a quote when the `taker` field is omitted. [Jupiter Swap API](https://developers.jup.ag/docs/api-reference/swap/order)

```powershell
$env:JUPITER_API_KEY = $null
.\.venv\Scripts\python.exe -m jupiter_bot paper --ticks 2 --db local/issue-2-smoke-2026-11-28.sqlite
.\.venv\Scripts\python.exe -m jupiter_bot paper --ticks 1 --db local/issue-2-smoke-2026-11-28.sqlite
```

| Tick time (UTC) | Run | Quote age at evaluation | Decision |
| --- | --- | ---: | --- |
| 00:42:42.350 | Initial | 166 ms | Hold |
| 00:43:42.524 | Initial | 126 ms | Hold |
| 00:43:42.992 | Restart | 111 ms | Hold |

The smoke run recorded three usable marks and three holds. The first process persisted two decisions, and the restart added one. The CLI events matched the three stored rows. The final balance was 100,000,000 simulated micro-USDC and zero lamports. The per-decision fill and reject rates were both 0/3 (0%). Per-intent rates were undefined because there were zero intents. All three ticks fell within the initial lookback. Quote age is `evaluatedMs - mark.observedMs`, measured from local request start. The runtime ledger and raw quote events are kept under ignored `local/` paths.

## Simulation assumptions and measurements before shadow work

- The fill output is the smaller of the quoted minimum and the expected output after a 50 bps haircut. The model treats the reported route fee as embedded and debits a fixed 20,000 micro-USDC (0.02 USDC) for other costs at each simulated fill. Verify fee treatment by route and measure platform and venue costs, signature and priority fees, rent, and price impact at actual order sizes.
- The freshness limit is 10 seconds from local request start to evaluation, with any source expiry also enforced. The loop settles in SQLite in the same cycle as the decision and models no chain execution delay. Measure upstream observation lag, route availability, decision-to-execution delay, quote expiry and missed fills before interpreting the simulation as execution-quality evidence.
- The mark extrapolates a fixed 0.1 SOL quote to the open inventory and accounts for an exit fee allowance. Measure how the executable price changes with size and how inventory is valued during data gaps. The synthetic round trip checks decision and ledger mechanics. The bounded public quote run checks the quote adapter and ledger over a short interval.
