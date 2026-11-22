# Jupiter paper trading bot

A local Solana SOL/USDC bot for replaying and paper trading a simple momentum rule. The team’s research goal is positive return after costs under repeatable evaluation. Keep the bot in paper mode while evidence is insufficient. This starter uses walletless quotes, simulated balances, and SQLite.

## First local check in PowerShell

Install Python 3.12 and Git, then run from the clone root. The starter was tested on Windows with Python 3.12.14. If your Python 3.12 executable is `py -3.12` or `python`, substitute it for `python3.12` after checking its version.

```powershell
python3.12 --version
python3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m jupiter_bot replay
```

A fresh replay produces six decisions, a simulated buy and sell, and ends at `99,369,197` micro-USDC and zero lamports. Reopening the same ledger and replaying the fixture reports six `duplicate_tick` results. To repeat from the starting balance, pass a new path such as `--db local/replay-second.sqlite`. CI installs test dependencies, then the tests run offline with socket connections blocked.

## Quote and paper trade

```powershell
.\.venv\Scripts\python.exe -m jupiter_bot quote
.\.venv\Scripts\python.exe -m jupiter_bot paper --ticks 5
```

The quote command defaults to selling 0.1 SOL. `quote --side buy` defaults to 10 USDC. `--amount` takes input mint base units. The client uses Swap API v2 `GET /order` with no `taker`, a five-second socket timeout, at least 2.1 seconds between requests within a process, and cooldown on rate-limit responses. If several processes share a network or key, stagger them. Setup works keyless. An optional `JUPITER_API_KEY` can be injected into the environment from your own secret store. `.env.example` lists the name for reference. The CLI reads the process environment. [Jupiter Swap API](https://developers.jup.ag/docs/swap) · [Rate limits](https://developers.jup.ag/docs/portal/rate-limits)

Paper waits the configured number of seconds after each tick. Omit `--ticks` for continuous operation. Each iteration logs JSON with the outcome and, when relevant, a sanitized failure reason. Check the event status during bounded runs. A tick can reject a failed quote and complete normally. Start a separate ledger when changing config. Keep your config and all ledgers on a local disk outside the vault and run one process per ledger.

```powershell
Copy-Item config.example.json config.local.json
.\.venv\Scripts\python.exe -m jupiter_bot paper --config config.local.json --db local/paper-custom.sqlite --ticks 5
New-Item -ItemType File -Path local/PAUSE -Force
Remove-Item -LiteralPath local/PAUSE
```

Leave the pause file in place while paused. It applies to all ledgers in that directory. Paused ticks still fetch marks and reconcile simulated equity. They skip settlement and avoid requesting the trade quote when already paused. Ctrl+C stops polling. Paper settlement and the intent record commit in one SQLite write, with zero pending amounts. Check the last JSON decision and balances before resuming. A different config, fixture, strategy or mode cannot reuse the same database.

## Strategy and accounting

- The only supported pair is wrapped SOL mint `So11111111111111111111111111111111111111112`, with 9 decimals, and USDC mint `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`, with 6 decimals. Amounts at the quote and ledger boundaries are integers in mint base units. The `*Usdc` fields mean micro-USDC. [Solana token mints](https://solana.com/docs/tokens)
- `momentum-v1` compares the expected output of a fixed 0.1 SOL sell probe with the mark three valid samples ago. With no SOL, a move of at least +100 bps signals a 10 USDC buy. With SOL, a move of at most −50 bps signals a capped sell. Missing or unusable marks and excessively spaced samples break the lookback. A signal requests its own exact-size quote. The quote adapter, rule, risk check, fill forecast, and atomic ledger write are separate steps.
- The example starts with 100 USDC and caps each trade at 25 USDC, SOL and aggregate exposure at 30 USDC each, daily turnover at 60 USDC, and daily mark-to-market loss at 5 USDC. Exposure covers SOL inventory valued at the current expected net probe price. Turnover uses the input USDC on buys or the larger of expected quoted and marked value on sells, plus a fixed fee allowance. Caps on extra exposure allow reducing sells that still pass the other checks. The quote age limit is 10 seconds from local request start, with source expiry respected when supplied. The configured slippage and absolute price-impact ceilings are 100 bps each.
- Simulated proceeds use the lower of the minimum quoted output and expected output reduced by 50 bps. The simulation charges 0.02 USDC separately per fill as an allowance for costs outside the quote. The reported route fee is treated as embedded in the quote. Equity adds cash to SOL valued at the conservative probe rate, with an exit fee allowance deducted from the SOL value and that position value floored at zero. The UTC day rolls on the first usable mark after midnight. Its opening equity is the last valid valuation, so overnight gaps count toward the new day’s loss. A stale, missing, or rejected mark leaves the last equity unchanged and blocks new trades. An invalid config or unusable ledger stops the CLI.
- Each decision records integer balances, the mark and trade quote when available, intent, rejection or simulated fill, and strategy, policy, and config identifiers. Field details are in the JSON output and the local `decisions` table. Treat local history and quote traffic as private. Keep any API or data charges in a separate service budget.

## Private GitHub setup checklist for the owner

- [ ] Review the files and first local checks. Run `git status --short --untracked-files=all` and `git remote -v`. This bootstrap initializes `main` locally. Create the initial commit yourself after inspecting the staged file list and diff for secrets, wallet files, live logs, databases, data and personal settings.
- [ ] If no remote is configured, use one of the creation paths below. If there is an existing remote to preserve, first verify its owner, visibility and history. Keep its URL and reconcile histories before pushing to it. Use a distinct remote name if connecting another repository.
- [ ] Verify the Private badge on the repository page or run `gh repo view OWNER/REPO --json nameWithOwner,visibility` and confirm `PRIVATE` and the intended owner. Then use Settings → Collaborators → Add people to invite exactly two teammates by GitHub username. Confirm both accept and review the access list. [Inviting GitHub collaborators](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/inviting-collaborators-to-a-personal-repository)
- [ ] Confirm Actions shows a green `offline-tests` job. Where the plan supports private-repo branch protection, apply it to `main`, require a PR, one teammate approval and the passing `offline-tests` status check, and apply it to administrators if available. On a private repo without protection, all three agree to the same PR and merge checks. GitHub can still run CI and record approvals, while teammates enforce the gating by checking both before each merge and avoiding direct pushes to `main`. [GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [ ] Post and assign the three [starter issue drafts](STARTER_ISSUES.md). Keep assignments and status in GitHub Issues, durable decisions and compact evidence in Obsidian, and commands in this README.

With no existing remote, after the owner has created the initial commit, replace `<owner>` and `<repo>` with your GitHub names:

```powershell
gh repo create <owner>/<repo> --private --source . --remote origin --push
```

The UI alternative is to choose New repository → Private and create an empty repository. Leave GitHub’s README, ignore and license initialization unchecked. Verify the Private badge, then run the following with your owner and repo substituted. [GitHub CLI repository creation](https://cli.github.com/manual/gh_repo_create)

```powershell
git remote add origin https://github.com/OWNER/REPO.git
git push -u origin main
```

## Teammate onboarding

1. Accept your invite and use your own GitHub identity and credentials.
2. Clone and run the first local check above.

   ```powershell
   git clone https://github.com/OWNER/REPO.git
   Set-Location REPO
   ```

3. In Obsidian choose **Open folder as vault**, select the clone’s `vault/` folder, and open `Home.md`. Git carries shared Markdown changes through PRs. Keep Obsidian configuration, cache and trash local. [Obsidian sync methods](https://obsidian.md/help/sync-notes)
4. Ask your AI coding assistant to read [AGENTS.md](AGENTS.md) at the start of each session if it does not load it automatically. Claim one issue and a scoped branch, then use the PR workflow in that file.

## Eventual wallet operations

Use the Jupiter website for manual trades from a personal wallet. In a future live stage, fund a dedicated low-balance bot wallet operated by one process and one owner. The operator sets permitted spot pairs by mint. If a manual trade from the bot wallet is necessary, pause submissions, resolve every pending submission, perform the swap, and reconcile balances, positions and remaining limits before resuming. To stop the funded bot, pause new submissions and monitor pending transactions until resolved. Review open positions with the wallet owner and use a separately approved exit plan. [Jupiter manual trading](https://docs.jup.ag/user-docs/trade/spot/manual-mode) · [Approval and reconciliation requirements](vault/Decisions.md)

## Evidence

- **paper** Offline synthetic replay exercises both directions. The tests cover caps, stale marks, pause and restart. One bounded paper tick recorded a fresh Jupiter mark and held at the starting balance. A separate keyless quote check verified Jupiter’s response to a walletless SOL/USDC request. This evidence covers mechanics and quote availability.
- **shadow** No execution-quality run.
- **live** No funded run or live swaps. After-cost returns remain unmeasured.

[Research plan](vault/Research.md) · [Decisions and unresolved assumptions](vault/Decisions.md)
