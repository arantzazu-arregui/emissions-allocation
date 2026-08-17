-- §6.3 / §7 -- allocation impacts rolled up to the groupings the paper reports.
--
-- Selin et al. align to the UNFCCC party list and report KP Annex B, OECD and EU27
-- aggregates. The Global Carbon Budget's `Regions` sheet supplies exactly those
-- memberships, so the grouping is data rather than a hand-maintained list.
--
-- A country belongs to SEVERAL groups at once -- Greece is EU27, OECD and KP Annex B
-- -- so `region_member` is many-to-many and a country's emissions appear once under
-- each group it belongs to. Summing across groups would double-count; each group is
-- a separate total.
--
-- The baseline is summed over the same membership, so dE% stays a like-for-like
-- ratio: allocated emissions over the group's own combined budget.
--
-- At n = 2 this is where the paper's OECD framing first says something. Vessel A
-- allocates entirely to China (neither OECD nor Annex B); vessel B's owner and
-- manager sit in the United Kingdom and Greece, both OECD. The rule that moves
-- emissions between those groups is the one the equity argument is about.

WITH allocated AS (
    SELECT
        m.region,
        a.option,
        a.year,
        a.scenario_id,
        a.power_estimate,
        a.smoothing_window,
        a.hk_treatment,
        sum(a.co2_mt)                       AS delta_e_mt,
        count(DISTINCT a.gcb_name)          AS n_countries,
        list(DISTINCT a.gcb_name)           AS countries
    FROM allocation    AS a
    JOIN region_member AS m ON m.country = a.gcb_name
    GROUP BY m.region, a.option, a.year, a.scenario_id,
             a.power_estimate, a.smoothing_window, a.hk_treatment
),
group_baseline AS (
    SELECT
        m.region,
        b.year,
        b.hk_treatment,
        sum(b.mtco2) AS baseline_mt
    FROM baseline      AS b
    JOIN region_member AS m ON m.country = b.country
    GROUP BY m.region, b.year, b.hk_treatment
)
SELECT
    a.region,
    a.option,
    a.year,
    a.scenario_id,
    a.power_estimate,
    a.smoothing_window,
    a.hk_treatment,
    a.delta_e_mt,
    g.baseline_mt,
    100.0 * a.delta_e_mt / nullif(g.baseline_mt, 0) AS delta_e_pct,
    a.n_countries,
    a.countries
FROM allocated      AS a
JOIN group_baseline AS g
  ON g.region       = a.region
 AND g.year         = a.year
 AND g.hk_treatment = a.hk_treatment
ORDER BY a.option, a.year, a.region, a.delta_e_mt DESC;
