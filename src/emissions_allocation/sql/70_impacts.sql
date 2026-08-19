-- §7.1 -- allocated emissions as additions to national carbon budgets.
--
--   dE_c        = E_c                                  [Mt CO2]
--   dE%_c       = 100 * dE_c / B_c                     [%]
--   rank_c      = RANK() OVER (PARTITION BY option, scenario ORDER BY dE_c DESC)
--   share_top20 = SUM(dE_c where rank <= 20) / SUM(dE_c)
--
-- Ranking and concentration are structurally meaningless at n = 2 -- with one or two
-- countries per option, every country ranks first or second and the top-20 share is
-- always 1.0. The code path exists, is exercised, and is labelled, because the
-- fleet-scale version is where it becomes informative.
--
-- The interpretable outputs at this scale are dE and dE% against the national
-- baselines selected by the paper-aligned territory map.
--
-- The join to `baseline` is on the GCB country NAME rather than ISO3, because that
-- is how the Global Carbon Budget keys its columns. It is an INNER join on purpose:
-- a country with no baseline must not silently acquire a null denominator and drop
-- out of the ranking. Missing baselines are caught in baselines.national_baseline.

WITH joined AS (
    SELECT
        a.option,
        a.country,
        a.gcb_name,
        a.year,
        a.scenario_id,
        a.power_estimate,
        a.smoothing_window,
        a.co2_mt                              AS delta_e_mt,
        b.mtco2                               AS baseline_mt,
        100.0 * a.co2_mt / nullif(b.mtco2, 0) AS delta_e_pct
    FROM allocation AS a
    JOIN baseline   AS b
      ON b.country      = a.gcb_name
     AND b.year         = a.year
)
SELECT
    option,
    country,
    gcb_name,
    year,
    scenario_id,
    power_estimate,
    smoothing_window,
    delta_e_mt,
    baseline_mt,
    delta_e_pct,
    RANK() OVER (
        PARTITION BY option, scenario_id, year
        ORDER BY delta_e_mt DESC
    ) AS rank_in_option,
    sum(delta_e_mt) OVER (
        PARTITION BY option, scenario_id, year
    ) AS total_allocated_mt,
    delta_e_mt / nullif(
        sum(delta_e_mt) OVER (
            PARTITION BY option, scenario_id, year
        ), 0
    ) AS share_of_allocated
FROM joined
ORDER BY option, year, scenario_id, delta_e_mt DESC;
