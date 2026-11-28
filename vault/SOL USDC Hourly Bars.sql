-- DuneSQL. Set start_utc and end_utc to UTC midnight timestamps.
-- The half-open interval includes start_utc and excludes end_utc.
WITH parameters AS (
    SELECT TIMESTAMP '{{start_utc}}' AS start_utc,
           TIMESTAMP '{{end_utc}}' AS end_utc,
           'So11111111111111111111111111111111111111112' AS sol,
           'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v' AS usdc
),
utc_window AS (
    SELECT * FROM parameters
    WHERE start_utc = date_trunc('day', start_utc)
      AND end_utc = date_trunc('day', end_utc)
      AND date_diff('day', start_utc, end_utc) BETWEEN 1 AND 178
),
variants AS (
    SELECT DISTINCT
        s.tx_id, s.log_index, s.block_time,
        s.input_mint = w.sol AS sol_sold,
        CASE WHEN s.input_mint = w.sol THEN s.input_amount ELSE s.output_amount END AS lamports,
        CASE WHEN s.input_mint = w.usdc THEN s.input_amount ELSE s.output_amount END AS micro_usdc
    FROM jupiter_solana.aggregator_swaps AS s
    CROSS JOIN utc_window AS w
    WHERE s.block_month >= CAST(date_trunc('month', w.start_utc) AS DATE)
      AND s.block_month <= CAST(date_trunc('month', w.end_utc - INTERVAL '1' second) AS DATE)
      AND s.block_time >= w.start_utc
      AND s.block_time < w.end_utc
      AND (
          (s.input_mint = w.sol AND s.output_mint = w.usdc)
          OR (s.input_mint = w.usdc AND s.output_mint = w.sol)
      )
      AND s.tx_id IS NOT NULL
      AND s.log_index IS NOT NULL
      AND s.input_amount > 0
      AND s.output_amount > 0
),
keyed_swaps AS (
    SELECT tx_id, log_index,
           COUNT(*) AS variant_count,
           date_trunc('hour', MIN(block_time)) AS bar_start_utc,
           MIN(lamports) AS lamports,
           MIN(micro_usdc) AS micro_usdc,
           bool_or(sol_sold) AS sol_sold
    FROM variants
    GROUP BY tx_id, log_index
),
hourly AS (
    SELECT bar_start_utc,
           COUNT_IF(variant_count = 1 AND sol_sold) AS sol_sold_swaps,
           COUNT_IF(variant_count = 1 AND NOT sol_sold) AS sol_bought_swaps,
           COUNT_IF(variant_count > 1) AS conflicting_swap_keys,
           SUM(CAST(lamports AS DECIMAL(38, 0))) FILTER (WHERE variant_count = 1) AS sol_lamports,
           SUM(CAST(micro_usdc AS DECIMAL(38, 0))) FILTER (WHERE variant_count = 1) AS usdc_microunits
    FROM keyed_swaps
    GROUP BY bar_start_utc
),
expected_hours AS (
    SELECT bar_start_utc
    FROM utc_window
    CROSS JOIN UNNEST(sequence(start_utc, end_utc - INTERVAL '1' hour, INTERVAL '1' hour)) AS t(bar_start_utc)
)
SELECT
    e.bar_start_utc,
    e.bar_start_utc + INTERVAL '1' hour AS bar_end_utc,
    e.bar_start_utc + INTERVAL '1' hour + INTERVAL '2' minute AS modeled_available_at_utc,
    COALESCE(h.sol_sold_swaps, 0) AS sol_sold_swaps,
    COALESCE(h.sol_bought_swaps, 0) AS sol_bought_swaps,
    COALESCE(h.conflicting_swap_keys, 0) AS conflicting_swap_keys,
    h.sol_lamports,
    h.usdc_microunits,
    CAST(1000.0 * CAST(h.usdc_microunits AS DOUBLE) / NULLIF(CAST(h.sol_lamports AS DOUBLE), 0)
         AS DECIMAL(24, 8)) AS vwap_usdc_per_sol
FROM expected_hours AS e
LEFT JOIN hourly AS h ON h.bar_start_utc = e.bar_start_utc
ORDER BY e.bar_start_utc
