-- §3.1 condition 2 -- is each vessel-hour inside a MARPOL Annex VI ECA?
--
-- Point-in-polygon against the Regulation 14 (SOx and particulate matter) areas:
-- six polygons -- Baltic Sea, US Caribbean, North American 1-3, and North Sea.
--
-- The shapefile PREDATES the Mediterranean SOx ECA, which entered into force in
-- May 2025. Irrelevant to a study period ending in 2024, but it must be added if
-- the horizon is ever extended -- and its absence would be silent, since a vessel
-- in the Mediterranean would simply never match.
--
-- Both ECA conditions are live for vessel A: 39 US calls (North American areas)
-- and 28 calls at Dutch, German and Belgian ports (North Sea area).
--
-- ST_Intersects rather than ST_Within so a position exactly on a boundary counts
-- as inside. At 0.01 degree cell centroids an exact-boundary hit is vanishingly
-- unlikely, but inclusive is the right default for a regulatory boundary.

SELECT
    h.imo,
    h.ts,
    -- A vessel-hour can sit inside only one of these areas in practice; ANY is used
    -- so an overlap could not duplicate the hour and double-count its fuel.
    (count(e.area) > 0)          AS in_eca,
    any_value(e.area)            AS eca_area
FROM vessel_hour AS h
LEFT JOIN eca_polygons AS e
       ON ST_Intersects(e.geom, ST_Point(h.lon, h.lat))
GROUP BY h.imo, h.ts
ORDER BY h.imo, h.ts;
