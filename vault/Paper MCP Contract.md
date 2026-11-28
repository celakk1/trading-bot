# Draft paper MCP contract

This draft covers local paper tools for [issue #3](https://github.com/celakk1/trading-bot/issues/3). Review the contract with the paper execution owner before implementation. A separate issue will cover implementation and offline tests.

## Tool interface

[Paper MCP Tool Schemas.json](Paper%20MCP%20Tool%20Schemas.json) contains JSON Schema 2020-12 definitions. Each entry in `tools` declares the input and output definitions for a `tools/list` descriptor. Publish each as an object schema with the referenced definition and its shared `$defs` attached at the schema root. Use the output schema for `structuredContent`. For compatibility, also return the serialized result in a text content block. Handle unknown tools and malformed requests as protocol errors. Report operational failures as tool errors with a sanitized reason code. A completed risk rejection is a valid proposal result. [MCP tools specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

| Tool | Behavior |
| --- | --- |
| `get_status` | Read the server-bound paper ledger head, server time, pause state and config and policy identifiers. A null head timestamp means the ledger has no decisions. |
| `get_positions` | Read simulated cash and SOL inventory at a consistent ledger head. Return valuation and its timestamp together, using nulls before the first usable mark. A prior valuation can remain visible during a market data gap. |
| `list_decisions` | Read up to 100 stored decisions in strictly descending tick order. Use an exclusive `beforeTickMs` cursor and return the last tick as the next cursor only when more rows exist. Project fields explicitly from the stored event. |
| `get_market_snapshot` | Read the latest recorded SOL-to-USDC probe. The tool calculates freshness at read time using the current maximum age and source expiry. A stale or rejected mark can remain visible with its status for diagnosis. A missing mark returns null. This read makes zero upstream requests. |
| `propose_trade` | Request a bounded risk evaluation of a paper candidate. The result states the supported input amount, expiry and reason. It has no settlement effect on balances, turnover or the normal strategy loop. |

Use Unix UTC milliseconds for timestamps. Carry atomic amounts as canonical decimal strings on the MCP wire so JavaScript clients can preserve all digits. Decode to integers and enforce the signed 64-bit limit before using values. SOL quantities are lamports and USDC quantities are micro-USDC. Read projections include no raw provider response, database path, credentials or free-text upstream errors. The server opens the configured local paper ledger for read projections. An empty ledger uses its configured starting balances.

## Proposal evaluation

1. Use the authenticated local caller and server-bound paper ledger as the scope. Require a bounded idempotency key, the exact mint pair and direction, a positive maximum input, the latest usable snapshot's tick as evidence, an absolute expiry and a short rationale. Treat the rationale as inert text and keep it out of tool responses and execution logs.
2. After validating the request shape, check its key against the proposal store. Same-key retries with the same arguments reuse the original ID and decision. An in-progress retry returns a bounded tool error with that ID. A changed payload under the same key returns an idempotency conflict. An expired retry returns the same ID with an expired status and no accepted size. For a new request, reject future evidence, expiry more than 60 seconds ahead, and evidence superseded by a new ledger tick. Atomically bind the caller, paper ledger, key and normalized request to a durable proposal ID before requesting any quotes. Return an expired result for a proposal that reaches its expiry before evaluation. Define the retention and crash recovery policy before implementation.
3. Read the latest state and pause flag, refresh the market mark and obtain an exact-size walletless quote for the candidate under the existing request limits. The server may lower the requested maximum to a candidate allowed by deterministic size limits. Evaluate that exact size with the existing deterministic risk gate. A further size change requires a matching fresh quote and the same gate. Recheck the ledger head, config identity, pause state, quote freshness and expiry at result time. Return a controlled rejection on stale state, risk failure or quote failure.
4. Set the result expiry to the earliest of the requested expiry, the remaining local freshness windows and any advertised expiry of the mark or candidate quote. Return a positive accepted size only for a passing result, always within the requested and policy bounds. The outcome is advisory even before expiry. Proposal evaluation persists proposal metadata only, in a store separate from the paper execution ledger.

## Tool boundary

The local server exposes only the five tool names in this draft. Signing, direct order submission, limit edits and live activation have no tool entry or permission path. The host supplies one paper ledger and policy at startup. Requests cannot select a file path, URL, wallet, mode, limit override or executable command. An unrecognized tool fails before dispatch, and unexpected object fields fail schema validation. A valid proposal can obtain quote data and a paper risk result only.

Review fixtures should cover an empty ledger, pagination boundaries, missing and stale marks, wrong mint direction, conflicting retries, expiry during quote refresh, a pause or state change during evaluation, and schema rejection of extra execution fields. Check that every forbidden tool name fails dispatch and that accepted proposals create no settlement or balance changes.
