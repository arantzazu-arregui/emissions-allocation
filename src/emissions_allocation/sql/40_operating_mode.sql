-- §4.1 -- IMO Table 16, the operational phase assignment decision matrix.
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
--   port. docs/METHODOLOGY.md §4.1 reproduces the column WITHOUT that restriction,
--   which would let a container ship count as At berth up to 5 nm out -- 1,300 kW
--   auxiliary instead of 1,800, over a hull that spends a quarter of the period in
--   port. The source is followed here; $is_liquid_tanker gates the column.
--
-- Speed-band boundaries are inclusive as the source states them -- "1<=",
-- "1-3 (incl. 3)", "3-5 (incl. 5)", ">5" -- i.e. a clean partition at 1/3/5 kn.
-- METHODOLOGY writes the bands without stating inclusivity.
--
-- ORDERING NOTE. The matrix takes main-engine LOAD as an input, while §4.2 zeroes
-- main-engine power in the At berth and Anchored MODES. That is circular as
-- written. It resolves because the matrix consults load only above 3 kn, where the
-- mode is never berth or anchored: load is computed from smoothed speed first,
-- used here, and only then zeroed where the mode requires it.
--
-- NULL distances mean "further than the prefilter searched", i.e. beyond 5 nm --
-- see 22_distance_to_port.sql and 23_distance_to_coast.sql.

SELECT
    h.imo,
    h.ts,
    h.sog,
    h.me_load,
    d.port_nm,
    d.coast_nm,
    CASE
        -- SOG <= 1 kn
        WHEN h.sog <= 1 THEN
            CASE
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
       ON d.lat = h.lat AND d.lon = h.lon;
