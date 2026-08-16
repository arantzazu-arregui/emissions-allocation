-- §1.7 -- observed coverage, per vessel per year.
--
--   coverage = observed hours / elapsed hours in period
--
-- Measured 8,782 / 8,784 = 99.98% for vessel A in 2024. Selin et al. interpolated
-- 32% of their hours from 2015 AIS; for a large container ship on major trade lanes
-- in the GFW era, gap-filling is close to unnecessary.
--
-- Computed here rather than taken from the GFW Insights endpoint, which cannot
-- reach before 2020-01-01 and counts "blocks" rather than hours. Where the two
-- overlap they agreed to within 0.08%.
--
-- `interpolated_hours` is reported alongside so a reader can see how much of the
-- series is filled rather than observed. §4.5 divides annual CO2 by `coverage`
-- when the correction is enabled.

SELECT
    imo,
    year(ts)                                      AS year,
    count(*) FILTER (WHERE NOT is_interpolated)   AS observed_hours,
    count(*) FILTER (WHERE is_interpolated)       AS interpolated_hours,
    count(*)                                      AS elapsed_hours,
    count(*) FILTER (WHERE NOT is_interpolated)
        / CAST(count(*) AS DOUBLE)                AS coverage
FROM vessel_hour
GROUP BY imo, year(ts)
ORDER BY imo, year;
