-- §4 -- IMO Table 16, the operational phase assignment decision matrix.
-- Fourth IMO GHG Study 2020, printed p.66 / PDF p.94.
--
-- Five phases from speed over ground, main-engine load, distance to port and
-- distance to coast.
--
-- THE COLUMNS ARE AN ORDERED LADDER -- the first applicable one wins:
--
--     port <= 1 nm  |  port 1-5 nm*  |  coast <= 1 nm  |  coast 1-5 nm  |  coast >= 5 nm
--
-- * The 'port 1-5 nm' column is footnoted in the source: "Applicable to chemical
--   tankers, liquified gas tankers, oil tankers and other liquids tankers only",
--   because liquid tankers are lightered offshore and so can berth within 5 nm of
--   port. The methodology records this restriction; $is_liquid_tanker gates the
--   column so it cannot classify a container or vehicle carrier as at berth up to
--   5 nm from port.
--
-- Speed-band boundaries are inclusive as the source states them -- "1<=",
-- "1-3 (incl. 3)", "3-5 (incl. 5)", ">5" -- i.e. a clean partition at 1/3/5 kn.
-- The methodology records these inclusive bounds.
--
-- ORDERING NOTE. The matrix takes main-engine LOAD as an input, while Section 4 zeroes
-- main-engine power in the At berth and Anchored MODES. That is circular as
-- written. It resolves because the matrix consults load only above 3 kn, where the
-- mode is never berth or anchored: load is computed from smoothed speed first,
-- used here, and only then zeroed where the mode requires it.
--
-- NULL distances mean "further than the prefilter searched", i.e. beyond 5 nm --
-- see 22_distance_to_port.sql and 23_distance_to_coast.sql.

-- ONE DOCUMENTED DEPARTURE FROM TABLE 16, at the At berth / Anchored split.
--
-- The source separates the two by distance to port because it had no better
-- signal, and METHODOLOGY Section 4 records the departure explicitly. We do have a better
-- signal: a GFW port-visit event asserts, from a different endpoint and with a
-- confidence score, that the vessel was in port between two timestamps.
--
-- Measured effect on vessel A: the distance test put 1,143 h at berth against
-- 17,427 h actually inside port visits, pushing ~16,000 h onto the Anchored
-- auxiliary load (1,800 kW) instead of At berth (1,300 kW). The cause is that GFW
-- anchorage coordinates sit some way off the berth, so a berthed ship reads as
-- more than 1 nm from "port".
--
-- $use_port_visit_intervals restores the strict distance rule when false, so the
-- two can be compared rather than one being assumed.
SELECT
    h.imo,
    h.ts,
    h.sog,
    h.me_load,
    d.port_nm,
    d.coast_nm,
    v.in_port_visit,
    v.visit_at_dock,
    d.coast_layer_loaded,
    CASE
        -- A null coast distance is meaningful only after a coastline layer was
        -- loaded and searched. Never silently interpret an absent layer as ocean.
        WHEN NOT coalesce(d.coast_layer_loaded, FALSE) THEN error(
            'coastline layer was not loaded; operating modes cannot be assigned'
        )
        -- SOG <= 1 kn
        WHEN h.sog <= 1 THEN
            CASE
                WHEN $use_port_visit_intervals AND v.in_port_visit THEN 'at_berth'
                WHEN d.port_nm <= 1                        THEN 'at_berth'
                WHEN $is_liquid_tanker AND d.port_nm <= 5  THEN 'at_berth'
                ELSE 'anchored'
            END

        -- 1 < SOG <= 3 kn
        WHEN h.sog <= 3 THEN 'anchored'

        -- 3 < SOG <= 5 kn
        WHEN h.sog <= 5 THEN
            CASE
                WHEN d.port_nm <= 1                        THEN 'manoeuvring'
                WHEN $is_liquid_tanker AND d.port_nm <= 5  THEN 'manoeuvring'
                WHEN d.coast_nm IS NOT NULL AND d.coast_nm < 5 THEN 'manoeuvring'
                WHEN h.me_load <= 0.65                     THEN 'slow_transit'
                ELSE 'normal_cruising'
            END

        -- SOG > 5 kn
        ELSE
            CASE
                WHEN d.port_nm <= 1                        THEN 'manoeuvring'
                WHEN h.me_load <= 0.65                     THEN 'slow_transit'
                -- The liquid-tanker column and every coast column agree above
                -- 5 kn at load > 0.65.
                ELSE 'normal_cruising'
            END
    END AS operating_mode
FROM hour_load AS h
LEFT JOIN position_distance AS d
       ON d.lat = h.lat AND d.lon = h.lon
LEFT JOIN port_visit_hour AS v
       ON v.imo = h.imo AND v.ts = h.ts;
