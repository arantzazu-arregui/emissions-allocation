-- Scenario table: the sensitivity cross join of METHODOLOGY §8.2.
--
-- Every downstream table is scenario-keyed, so the power/speed estimates and the
-- smoothing-window range propagate through the model without any stage needing to
-- know how many scenarios there are.
--
-- The set is built from config/pilot.yaml, not hard-coded here: estimate C joins
-- automatically the day someone supplies a sourced installed power and service
-- speed for a hull (OPEN ITEM 4).
--
-- Written as an explicit CROSS JOIN rather than two UNNESTs in one SELECT list.
-- DuckDB zips multiple UNNESTs positionally -- 2 x 4 would silently yield 2
-- rows instead of 8, and every downstream aggregate would quietly cover a
-- fraction of the intended scenario space.

CREATE OR REPLACE TABLE scenario AS
SELECT
    power_estimate || '_w' || smoothing_window AS scenario_id,
    power_estimate,                   -- A (EEXI curve fit) | B (Admiralty) | C (sourced)
    CAST(smoothing_window AS INTEGER) AS smoothing_window
FROM      (SELECT UNNEST($power_estimates)   AS power_estimate)   AS p
CROSS JOIN (SELECT UNNEST($smoothing_windows) AS smoothing_window) AS w
ORDER BY power_estimate, smoothing_window;
