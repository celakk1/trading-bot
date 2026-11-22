# Starter issue drafts

Assign one draft to each teammate. Post them in the private repository after onboarding.

## 1. Freeze the SOL/USDC dataset and baseline protocol

**Default assignee:** Research teammate. Own the data manifest, read-only data queries and baseline experiment reports under `vault/`. Coordinate changes to `vault/Research.md` with the team.

- [ ] Check export terms, coverage and billing without incurring charges. Publish the approved dataset manifest and query, with UTC windows, schema, extraction date, freshness, checksums and permitted uses.
- [ ] Freeze bars, chronological folds, purge rules, held-out window, costs, the shared trial budget and acceptance thresholds before tuning.
- [ ] Document how all three PCs can reproduce the same small slice without copying full history. Report the cash, buy-and-hold and momentum results under the shared protocol once the slice is available.

## 2. Verify paper execution and risk in a repeatable run

**Default assignee:** Paper execution teammate. Own `jupiter_bot/`, `tests/` and `config.example.json`. This teammate owns cross-cutting ledger and shared config changes.

- [ ] Reproduce the fixture and duplicate-on-restart outcome. Capture cap, pause, stale and missing-market evidence. Verify the paper loop resumes without duplicate settlement.
- [ ] Run the bounded keyless paper smoke check. Record quote age, decision counts and fill/reject rates with a clear evidence label. Keep runtime ledger and quotes out of Git.
- [ ] Document what the simulation assumes about fees, adverse prices and latency, and what remains to be measured before shadow work.

## 3. Validate onboarding and draft the MCP contract

**Default assignee:** Setup and CI teammate. Own `README.md`, `AGENTS.md`, `.github/workflows/` and the later MCP contract note. Consult the execution owner about ledger and config.

- [ ] Follow the instructions from a clean clone, open the vault as a folder, and verify the offline check on a teammate’s PC and in Actions.
- [ ] Confirm the owner’s review and CI rules for the private repo. Confirm ignore rules keep local credentials, datasets, logs and SQLite out of the staged file list.
- [ ] After the paper loop is stable, draft tool input and output schemas for read-only status, positions, decisions and market snapshots, plus expiring paper trade proposals. Show how the contract denies signing, direct orders, limit edits and live activation. Keep implementation under a later issue.
