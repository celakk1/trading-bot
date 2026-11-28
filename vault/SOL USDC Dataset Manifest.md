# SOL/USDC dataset manifest

Dataset proposal `jupiter-sol-usdc-hourly-v1` for [issue #1](https://github.com/celakk1/trading-bot/issues/1). Review the source and the companion [baseline protocol](SOL%20USDC%20Baseline%20Protocol.md) before treating this as a frozen historical dataset.

| Field | Value |
| --- | --- |
| Source | Dune `jupiter_solana.aggregator_swaps`. The source records completed Jupiter swaps across underlying routes. [Dune table documentation](https://docs.dune.com/data-catalog/curated/dex-trades/solana/jupiter-aggregator-trades) |
| Query | [SOL USDC Hourly Bars.sql](SOL%20USDC%20Hourly%20Bars.sql). Matches both directions by mint, counts distinct transaction and log-index pairs, and aggregates remotely before export. |
| Pair | Wrapped SOL `So11111111111111111111111111111111111111112` with 9 decimals and USDC `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v` with 6 decimals. |
| Full UTC range | `[2026-06-01 00:00:00, 2026-11-26 00:00:00)`. Expected grid is 4,272 hours. |
| Verification slice | `[2026-06-08 00:00:00, 2026-06-15 00:00:00)`. Expected grid is 168 hours. Verify this slice first without scoring strategies. |
| Extraction and freshness | Pending an authorized export. Record the extraction time UTC, Dune execution ID, observed freshness and the latest upstream bar actually present. Two minutes after bar end is the modeled availability for evaluation. Provider ingest lag was not measured. |
| Coverage | Observed row count, non-empty hour count, missing hours, conflict count and longest gap are pending. The query emits empty hours, exposes conflicting duplicate keys, and counts swaps separately by direction. It filters out incomplete or nonpositive swap rows. |
| Integrity | Record the SQL Git commit, SHA-256 of the frozen Parquet file and the canonical row digest for each approved slice. No historical dataset digest exists until extraction. |

The schema uses UTC timestamps without a timezone offset in Dune. Interpret them as UTC on import. Each row has `bar_start_utc`, `bar_end_utc` and `modeled_available_at_utc` timestamps, integer `sol_sold_swaps`, `sol_bought_swaps` and `conflicting_swap_keys`, decimal integer `sol_lamports` and `usdc_microunits`, and decimal `vwap_usdc_per_sol` with eight places. The last three are null for an empty hour. The hourly price is total USDC divided by total SOL after mint scaling. It describes historical swap prints. The baseline models execution costs and delays separately.

## Access, costs, and permitted use

Dune lists free access after a trial as view-only and lists API or CSV export and compute as credit-bearing. No Dune account, query execution, data export, or paid service was used to prepare this manifest. Before any authorized run, confirm the account's export entitlement, available credits, applicable terms, and cost cap. Record measured execution and export credits for the 168-hour slice before scaling. [Dune billing](https://docs.dune.com/api-reference/overview/billing) · [Dune terms](https://dune.com/terms)

Keyless GeckoTerminal metadata confirmed the exact mint pair for Orca pool `Czfq3xZZDmsdGdUyrNLtRhGc47cXcZtLG4crryfu44zE`. Its bars cover one pool. The published API terms impose storage and reuse conditions, so the team still needs an appropriate permission basis for any durable shared snapshot. No GeckoTerminal bar extract was taken. [GeckoTerminal Public API](https://api.geckoterminal.com/docs/index.html) · [CoinGecko API terms](https://www.coingecko.com/en/api_terms)

The proposed use is private, offline methodology and paper evaluation. Before approving a dataset version, record the applicable provider terms for local copies, private teammate access, retention, and aggregate publication. Share the SQL and manifest through Git. Keep credentials, export data, and private storage locations out of Git.

## Reproduce the small slice

1. Use the same reviewed SQL commit and the two verification timestamps above. One authorized maintainer runs the bounded query and exports only its hourly output to a local file. Filter exported results by actual timestamp before evaluation. Leave the final holdout in a separate, unopened result until the baseline is selected.
2. Run the offline [export checker](verify_hourly_bars.py). It checks for the exact UTC grid, distinguishes empty hours from missing rows, and reports conflicting keys, the longest gap, and the most recent nonempty bar. Install DuckDB in a research environment and add `--parquet` to create a typed local Parquet file with explicit UTC timestamps. Keep both files outside Git and discard the working CSV when it is no longer needed under the provider’s terms.
3. The maintainer records extraction UTC, query execution ID, reported counts, approved retention, and the two hashes in the report. Each PC uses the same approved small file from the private source if sharing is permitted, or repeats the identical bounded export under its own entitlement. Use `canonical_sha256` to compare independent exports and `parquet_sha256` to verify a copied artifact. Byte hashes may differ for independently written Parquet files. Treat content mismatches as new versions and investigate before scoring.
4. Keep at most 256 MiB of local replaceable slices per PC and retain them at most seven days, or less if the provider's terms require it. All three PCs should verify the same dataset version and digest before comparing results.

Run from the repository root. The first call only needs Python 3.12. The second requires DuckDB (`2.0.1` was verified) and a fresh output path.

```powershell
py -3.12 vault/verify_hourly_bars.py local/verification.csv --start 2026-06-08 --end 2026-06-15
py -3.12 vault/verify_hourly_bars.py local/verification.csv --start 2026-06-08 --end 2026-06-15 --parquet local/verification.parquet
```

The canonical digest covers a UTF-8 CSV representation with the query’s header and column order, rows sorted by hour, ISO UTC timestamps with a trailing `Z`, decimal integer volumes, eight fractional price digits, empty nulls, and LF line endings. The checker reports data through the last nonempty bar. Actual ingest lag needs a separate measurement.

For the full study, use the exact 4,272-hour grid across the development and later holdout extracts. Run with `--for-scoring` on the local export for each scored interval. The proposed minimum is 99% usable hours, zero conflicting keys, and no gap longer than two consecutive hours. If the check fails, record the affected range and review a new data version before scoring. Preserve the failed coverage report.
