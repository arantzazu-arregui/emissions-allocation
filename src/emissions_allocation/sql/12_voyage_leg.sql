-- §1 output: voyage_leg -- one row per consecutive port pair.
--
-- A leg is the passage between the END of one port call and the START of the next,
-- so `depart_ts` comes from the previous call's end and `arrive_ts` from this call's
-- start. Legs are built with LAG over port_call ordered by time, per vessel.
--
-- `is_eu_eu` is the reason this table exists. §3.1 assigns distillate fuel to any
-- hour on a leg between two EU ports, and reading that from an actual port-call
-- sequence is a genuine improvement on the EEZ proxy that gridded-only data would
-- have forced. Vessel A has 27 consecutive EU->EU legs across the study period, so
-- the rule genuinely fires.
--
-- Note the origin country is the PREVIOUS call's *end* anchorage flag rather than
-- its start: a visit can begin at one anchorage and end at another (GFW models
-- start, intermediate and end anchorages separately), and the ship departs from
-- where it ended.

WITH ordered AS (
    SELECT
        imo,
        start_ts,
        end_ts,
        port_id,
        port_iso3,
        port_name,
        end_port_id,
        end_port_iso3,
        LAG(end_ts)        OVER w AS prev_end_ts,
        LAG(end_port_id)   OVER w AS prev_port_id,
        LAG(end_port_iso3) OVER w AS prev_port_iso3
    FROM port_call
    WINDOW w AS (PARTITION BY imo ORDER BY start_ts)
)
SELECT
    imo,
    prev_end_ts                       AS depart_ts,
    start_ts                          AS arrive_ts,
    prev_port_id                      AS origin_port_id,
    prev_port_iso3                    AS origin_iso3,
    port_id                           AS dest_port_id,
    port_iso3                         AS dest_iso3,
    port_name                         AS dest_port_name,
    datediff('second', prev_end_ts, start_ts) / 3600.0 AS leg_hours,
    -- §3.1 condition 3.
    (   list_contains($eu27, prev_port_iso3)
    AND list_contains($eu27, port_iso3)) AS is_eu_eu,
    -- An international leg is unambiguous when the two ports sit in different
    -- countries. §5.4's ">95% of hours in one EEZ" test is kept as the cross-check
    -- because the fleet-scale version needs it.
    (prev_port_iso3 IS DISTINCT FROM port_iso3) AS is_international
FROM ordered
-- The first call of each vessel has no predecessor and so begins no leg.
WHERE prev_end_ts IS NOT NULL
ORDER BY imo, arrive_ts;
