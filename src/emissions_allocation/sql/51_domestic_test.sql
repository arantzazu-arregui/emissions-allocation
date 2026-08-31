-- §2 -- international versus domestic classification.
--
-- Selin et al. classify a ship as DOMESTIC when more than 95% of all active
-- yearly AIS signals fall inside a single country's EEZ. High-seas signals are
-- therefore part of the denominator, even though they cannot belong to a
-- dominant EEZ. Everything else is international.
--
-- The test is trivially satisfied at this scale -- vessel A calls at ports in
-- seventeen countries -- but it is implemented because the fleet-scale version
-- needs it, and because a template that omits the filter would quietly include
-- domestic craft when scaled.
--
-- Out-of-service hours are excluded. A hull laid up in one country's waters for
-- 282 days would otherwise drift toward looking domestic on the strength of hours
-- it spent not trading.

WITH totals AS (
    SELECT
        h.imo,
        count(*)                                          AS active_hours_total,
        count(*) FILTER (WHERE e.eez_iso3 IS NOT NULL)    AS hours_in_any_eez,
        sum(CASE WHEN e.is_disputed THEN 1 ELSE 0 END)    AS hours_disputed
    FROM vessel_hour AS h
    LEFT JOIN eez_hour AS e ON e.imo = h.imo AND e.ts = h.ts
    WHERE NOT h.is_inactive
    GROUP BY h.imo
),
hours AS (
    SELECT
        e.imo,
        e.eez_iso3,
        count(*)                                          AS hours_in_eez
    FROM eez_hour AS e
    JOIN vessel_hour AS h ON h.imo = e.imo AND h.ts = e.ts
    WHERE NOT h.is_inactive
      AND e.eez_iso3 IS NOT NULL
    GROUP BY e.imo, e.eez_iso3
),
ranked AS (
    SELECT
        t.imo,
        h.eez_iso3,
        coalesce(h.hours_in_eez, 0)                        AS hours_in_eez,
        t.active_hours_total,
        t.hours_in_any_eez,
        t.hours_disputed,
        coalesce(h.hours_in_eez, 0) / CAST(t.active_hours_total AS DOUBLE) AS share,
        row_number() OVER (
            PARTITION BY t.imo
            ORDER BY coalesce(h.hours_in_eez, 0) DESC, h.eez_iso3
        ) AS rn
    FROM totals AS t
    LEFT JOIN hours AS h ON h.imo = t.imo
)
SELECT
    imo,
    eez_iso3                       AS dominant_eez_iso3,
    hours_in_eez                   AS dominant_eez_hours,
    active_hours_total,
    hours_in_any_eez,
    hours_disputed,
    share                          AS dominant_eez_share,
    -- Selin et al.'s all-active-signals rule.
    (share > $domestic_threshold)  AS is_domestic,
    NOT (share > $domestic_threshold) AS is_international
FROM ranked
WHERE rn = 1
ORDER BY imo;
