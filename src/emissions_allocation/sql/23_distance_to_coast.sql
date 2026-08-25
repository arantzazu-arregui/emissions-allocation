-- §5.2 -- distance from each position to the nearest coast, in nautical miles.
--
-- OPEN ITEM 3, resolved. Marine Regions "Marine and Land Zones v4" ships as
-- EEZ_land_union: 328 polygons, each a country's LAND MERGED WITH ITS EEZ. It is
-- not a coastline layer, so land is recovered by differencing it against the EEZ
-- polygons already loaded for §6:
--
--     land_c = union_c MINUS eez_c        joined on MRGID_EEZ
--
-- and distance-to-coast is then the distance to the nearest land polygon. A
-- position inside land (a harbour) gets 0, which is the correct reading -- a ship
-- at berth is at the coast.
--
-- CAVEAT: EEZ v12 is measured from the territorial-sea BASELINE, so internal
-- waters landward of it fall into the difference and read as land. Distance is
-- therefore measured from the baseline, not the physical shoreline. For vessel A
-- that affects 0.2% of distinct positions, all in harbours already inside 1 nm of
-- a port, so no Table 16 column changes. The IMO study uses Natural Earth
-- shorelines, which would not have this caveat.
--
-- Only positions within `$prefilter_degrees` of land are computed. Everything else
-- is beyond the 5 nm threshold, where Table 16 stops distinguishing -- so the exact
-- distance is irrelevant and computing it for 36,000 mid-ocean positions would be
-- wasted work. Those rows come back NULL and the mode matrix reads NULL as ">= 5".
--
-- ST_ClosestPoint is planar, which is accurate to millimetres over a <10 nm
-- neighbourhood; the distance itself is then great-circle. ST_Distance_Spheroid
-- is not used because it accepts only POINT_2D and the CRS-tagged geometry
-- returned by ST_ClosestPoint cannot be cast to it.

WITH nearest AS (
    SELECT
        p.lat,
        p.lon,
        gc_nm(
            p.lat, p.lon,
            ST_Y(ST_ClosestPoint(l.geom, ST_Point(p.lon, p.lat))),
            ST_X(ST_ClosestPoint(l.geom, ST_Point(p.lon, p.lat)))
        ) AS nm
    FROM distinct_position AS p
    JOIN coast_land AS l
      ON ST_DWithin(l.geom, ST_Point(p.lon, p.lat), $prefilter_degrees)
)
SELECT lat, lon, min(nm) AS coast_nm
FROM nearest
GROUP BY lat, lon;
