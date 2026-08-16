-- §5.4 -- international versus domestic classification.
--
-- Selin et al. classify a ship as DOMESTIC when more than 95% of its signals fall
-- inside a single country's EEZ. Everything else is international, and only
-- international shipping is in scope for national allocation.
--
-- The test is trivially satisfied at this scale -- vessel A calls at ports in
-- seventeen countries -- but it is implemented because the fleet-scale version
-- needs it, and because a template that omits the filter would quietly include
-- domestic craft when scaled.
--
-- Out-of-service hours are excluded from the denominator. A hull laid up in one
-- country's waters for 282 days would otherwise drift toward looking domestic on
-- the strength of hours it spent not trading.

WITH hours AS (
    SELECT
        e.imo,
        e.eez_iso3,
        count(*)                                          AS hours_in_eez,
        sum(count(*)) OVER (PARTITION BY e.imo)           AS hours_total,
        sum(CASE WHEN e.is_disputed THEN 1 ELSE 0 END)    AS hours_disputed
    FROM eez_hour AS e
    JOIN vessel_hour AS h ON h.imo = e.imo AND h.ts = e.ts
    WHERE NOT h.is_inactive
      AND e.eez_iso3 IS NOT NULL
    GROUP BY e.imo, e.eez_iso3
),
ranked AS (
    SELECT
        imo,
        eez_iso3,
        hours_in_eez,
        hours_total,
        hours_disputed,
        hours_in_eez / CAST(hours_total AS DOUBLE)        AS share,
        row_number() OVER (PARTITION BY imo ORDER BY hours_in_eez DESC) AS rn
    FROM hours
)
SELECT
    imo,
    eez_iso3                       AS dominant_eez_iso3,
    hours_in_eez                   AS dominant_eez_hours,
    hours_total                    AS hours_in_any_eez,
    hours_disputed,
    share                          AS dominant_eez_share,
    -- The paper's rule, verbatim.
    (share > $domestic_threshold)  AS is_domestic,
    NOT (share > $domestic_threshold) AS is_international
FROM ranked
WHERE rn = 1
ORDER BY imo;
