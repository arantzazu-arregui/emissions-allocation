"""§5 allocation, §6 baselines and §7 impacts.

§4 is blocked on the coastline layer, so these exercise the chain with a synthetic
``emissions_year`` -- the point is to prove the joins, the aggregation and the Hong
Kong treatments, all of which are independent of what the emissions number turns
out to be.

Two external anchors are used as real ground truth:

* the **Global Carbon Budget** workbook, for the baseline figures §6.4 quotes;
* **Selin et al.'s supplementary Table 1**, for the paper's own country alignment.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from emissions_allocation import baselines, impacts
from emissions_allocation.allocation import (
    ALLOCATION_OPTIONS,
    DOMESTIC_THRESHOLD,
    allocate,
    summarise_options,
    vessel_key_table,
)
from emissions_allocation.config import ConfigError, load_config
from emissions_allocation.db import Database

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SELIN_SUPP = PROJECT_ROOT / "data" / "external" / "paper" / "erlabec02supp2.xls"
VESSEL_A = "9516454"


@pytest.fixture(scope="module")
def cfg():
    return load_config()


@pytest.fixture(scope="module")
def gcb(cfg):
    return baselines.build_baselines(cfg)


@pytest.fixture(scope="module")
def synthetic_emissions(cfg):
    """One vessel-year per scenario, 100,000 t CO2 each.

    A round number so every downstream figure can be checked by hand.
    """
    rows = [
        {
            "imo": VESSEL_A,
            "year": year,
            "scenario_id": s["scenario_id"],
            "power_estimate": s["power_estimate"],
            "smoothing_window": s["smoothing_window"],
            "co2_tonnes": 100_000.0,
        }
        for year in (2023, 2024)
        for s in cfg.scenarios()
        if s["hk_treatment"] == "separate"   # emissions do not depend on the treatment
    ]
    return pd.DataFrame(rows).drop_duplicates(
        subset=["imo", "year", "power_estimate", "smoothing_window"]
    )


# ---------------------------------------------------------------------------
# §6 -- baselines
# ---------------------------------------------------------------------------


def test_gcb_reproduces_the_documented_2024_baselines(gcb) -> None:
    """§6.4: Hong Kong 33.3 Mt CO2, China 12,289 Mt CO2."""
    separate = gcb[gcb["hk_treatment"] == "separate"]
    hk = separate[(separate["country"] == "Hong Kong") & (separate["year"] == 2024)]
    cn = separate[(separate["country"] == "China") & (separate["year"] == 2024)]
    assert hk.iloc[0]["mtco2"] == pytest.approx(33.3, abs=0.1)
    assert cn.iloc[0]["mtco2"] == pytest.approx(12_289, rel=0.001)


def test_hong_kong_and_china_differ_by_about_369x(gcb) -> None:
    """The gap that makes the treatment decisive for a Hong Kong-flagged hull."""
    separate = gcb[(gcb["hk_treatment"] == "separate") & (gcb["year"] == 2024)]
    hk = separate[separate["country"] == "Hong Kong"].iloc[0]["mtco2"]
    cn = separate[separate["country"] == "China"].iloc[0]["mtco2"]
    assert cn / hk == pytest.approx(369, rel=0.02)


def test_units_are_converted_from_carbon_to_co2(gcb) -> None:
    row = gcb[(gcb["country"] == "China") & (gcb["year"] == 2024)].iloc[0]
    assert row["mtco2"] / row["mtc"] == pytest.approx(3.664)


def test_folding_sums_hong_kong_into_china(gcb) -> None:
    """Folding must ADD the baselines, not discard Hong Kong's."""
    sep = gcb[(gcb["hk_treatment"] == "separate") & (gcb["year"] == 2024)]
    folded = gcb[(gcb["hk_treatment"] == "folded_into_china") & (gcb["year"] == 2024)]
    hk = sep[sep["country"] == "Hong Kong"].iloc[0]["mtco2"]
    cn = sep[sep["country"] == "China"].iloc[0]["mtco2"]
    cn_folded = folded[folded["country"] == "China"].iloc[0]["mtco2"]
    assert cn_folded == pytest.approx(cn + hk, rel=1e-6)


def test_hong_kong_disappears_when_folded(gcb) -> None:
    folded = gcb[gcb["hk_treatment"] == "folded_into_china"]
    assert folded[folded["country"] == "Hong Kong"].empty


def test_baselines_cover_the_study_period(gcb) -> None:
    assert set(gcb["year"]) == set(range(2017, 2025))


def test_unknown_treatment_raises(gcb) -> None:
    with pytest.raises(ConfigError, match="unknown Hong Kong treatment"):
        baselines.apply_hk_treatment(gcb, "ignore_it")


def test_missing_country_raises_rather_than_returning_zero(gcb) -> None:
    """A zero denominator would make dE% infinite; a dropped country vanishes."""
    with pytest.raises(ConfigError, match="no Global Carbon Budget baseline"):
        baselines.national_baseline(gcb, "Atlantis", 2024, "separate")


def test_international_shipping_cross_check(cfg) -> None:
    """§6.2: 170.15 MtC = 623 Mt CO2 for 2024, an independent global estimate."""
    out = baselines.shipping_cross_check(cfg, 2024)
    assert out["mtc"] == pytest.approx(170.15, abs=0.1)
    assert out["mtco2"] == pytest.approx(623, abs=1.0)


def test_regions_sheet_supplies_the_papers_groupings(cfg) -> None:
    """Note this sheet has NO header row, unlike Territorial Emissions."""
    regions = baselines.load_regions(cfg)
    assert {"KP Annex B", "OECD", "EU27"} <= set(regions)
    assert "Germany" in regions["EU27"]
    assert "China" not in regions["OECD"]


# ---------------------------------------------------------------------------
# Selin et al. supplementary Table 1 -- the paper's own country alignment
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def selin():
    if not SELIN_SUPP.exists():
        pytest.skip("Selin supplementary Table 1 not present")
    frame = pd.read_excel(SELIN_SUPP, sheet_name="All", header=0)
    frame.columns = [str(c).strip() for c in frame.columns]
    return frame


def test_paper_has_no_hong_kong_row(selin) -> None:
    """Open item 2, closed. The paper folds Hong Kong into China.

    199 countries, aligned to the UNFCCC party list -- no Hong Kong, no Taiwan, no
    Macao. So `folded_into_china` is the replication-faithful treatment, and under
    it vessel A's allocation is fully degenerate.
    """
    names = selin["Name_ref"].astype(str)
    assert not names.str.contains("Hong Kong", case=False).any()
    assert not names.str.contains("Taiwan", case=False).any()
    assert not names.str.contains("Macao|Macau", case=False).any()
    assert names.str.contains("China", case=False).any()


def test_paper_reports_all_five_allocation_options(selin) -> None:
    """Four are computable here; bunker is a fleet-scale construct."""
    for column in ("Flag_MT", "Owner_MT", "Operator_MT", "Manager_MT", "Bunker_MT"):
        assert column in selin.columns


def test_paper_country_count_is_the_unfccc_party_list(selin) -> None:
    assert len(selin) == 199


def test_open_registries_carry_outsized_flag_allocations(selin) -> None:
    """The paper's equity finding, visible in its own supplementary table.

    Panama, Liberia and the Marshall Islands take far more under the flag rule than
    under owner -- which is exactly the divergence vessel B is meant to reproduce,
    and which vessel A cannot show.
    """
    registries = selin[selin["Name_ref"].isin(["Panama", "Liberia", "Marshall Islands"])]
    assert len(registries) == 3
    assert (registries["Flag_MT"] > registries["Owner_MT"]).all()


# ---------------------------------------------------------------------------
# §5 -- allocation keys
# ---------------------------------------------------------------------------


def test_vessel_key_table_covers_every_option(cfg) -> None:
    table = vessel_key_table(cfg, "separate")
    assert set(table["option"]) == set(ALLOCATION_OPTIONS)
    assert len(table) == len(cfg.vessels) * len(ALLOCATION_OPTIONS)


def test_bunker_is_not_an_allocation_option() -> None:
    """Not computable at this scale -- by construction, not omission."""
    assert "bunker" not in ALLOCATION_OPTIONS


def test_hong_kong_flag_maps_to_china_when_folded(cfg) -> None:
    separate = vessel_key_table(cfg, "separate")
    folded = vessel_key_table(cfg, "folded_into_china")
    flag_sep = separate[(separate["imo"] == VESSEL_A) & (separate["option"] == "flag")]
    flag_fold = folded[(folded["imo"] == VESSEL_A) & (folded["option"] == "flag")]
    assert flag_sep.iloc[0]["gcb_name"] == "Hong Kong"
    assert flag_fold.iloc[0]["gcb_name"] == "China"
    # The ISO3 key is unchanged -- only the baseline it joins to moves.
    assert flag_sep.iloc[0]["country"] == flag_fold.iloc[0]["country"] == "HKG"


def test_vessel_a_is_degenerate_when_hong_kong_folds(cfg) -> None:
    """All four options resolve to China -- the paper's own treatment."""
    summary = summarise_options(cfg, "folded_into_china")
    row = summary[summary["imo"] == VESSEL_A].iloc[0]
    assert row["is_degenerate"]


def test_vessel_a_splits_three_to_one_when_hong_kong_is_separate(cfg) -> None:
    summary = summarise_options(cfg, "separate")
    row = summary[summary["imo"] == VESSEL_A].iloc[0]
    assert row["n_distinct_countries"] == 2
    assert not row["is_degenerate"]
    assert row["flag"] == "HKG"
    assert row["owner"] == row["manager"] == row["operator"] == "CHN"


def test_operator_key_is_marked_a_proxy(cfg) -> None:
    """§5.2: Equasis has no operator field; commercial manager stands in."""
    table = vessel_key_table(cfg, "separate")
    assert table[table["option"] == "operator"]["is_proxy"].all()
    assert not table[table["option"] == "owner"]["is_proxy"].any()


# ---------------------------------------------------------------------------
# §5.3 and §7 -- the chain, on synthetic emissions
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def chain(cfg, gcb, synthetic_emissions):
    with Database() as db:
        allocation = allocate(db, cfg, synthetic_emissions)
        result = impacts.compute_impacts(db, allocation, gcb)
    return allocation, result


def test_allocation_assigns_the_full_total_to_one_country_per_option(chain) -> None:
    allocation, _ = chain
    one = allocation[
        (allocation["option"] == "owner")
        & (allocation["year"] == 2024)
        & (allocation["hk_treatment"] == "separate")
    ]
    assert set(one["country"]) == {"CHN"}
    assert one["co2_tonnes"].unique().tolist() == [100_000.0]


def test_allocation_covers_every_scenario(chain, cfg) -> None:
    allocation, _ = chain
    n_emission_scenarios = len(cfg.run["power_estimates"]) * len(cfg.run["smoothing_windows"])
    subset = allocation[
        (allocation["option"] == "flag")
        & (allocation["year"] == 2024)
        & (allocation["hk_treatment"] == "separate")
    ]
    assert len(subset) == n_emission_scenarios


def test_delta_e_percent_against_hong_kong_is_369x_that_against_china(chain) -> None:
    """The headline methodological finding: identical tonnage, two denominators."""
    _, result = chain
    flag_2024 = result[(result["option"] == "flag") & (result["year"] == 2024)]
    hk = flag_2024[flag_2024["hk_treatment"] == "separate"].iloc[0]
    cn = flag_2024[flag_2024["hk_treatment"] == "folded_into_china"].iloc[0]
    assert hk["baseline_mt"] == pytest.approx(33.3, abs=0.1)
    assert cn["baseline_mt"] == pytest.approx(12_322, rel=0.01)
    assert hk["delta_e_pct"] / cn["delta_e_pct"] == pytest.approx(370, rel=0.02)


def test_delta_e_percent_is_computed_correctly(chain) -> None:
    _, result = chain
    row = result[
        (result["option"] == "owner")
        & (result["year"] == 2024)
        & (result["hk_treatment"] == "separate")
    ].iloc[0]
    assert row["delta_e_pct"] == pytest.approx(
        100.0 * row["delta_e_mt"] / row["baseline_mt"]
    )


def test_one_ship_is_a_vanishing_share_of_chinas_budget(chain) -> None:
    """100,000 t against 12,289 Mt is ~0.0008%. Sanity, not a result."""
    _, result = chain
    row = result[
        (result["option"] == "owner")
        & (result["year"] == 2024)
        & (result["hk_treatment"] == "separate")
    ].iloc[0]
    assert row["delta_e_pct"] < 0.01


def test_ranking_runs_but_is_meaningless_at_this_scale(chain) -> None:
    _, result = chain
    assert (result["rank_in_option"] >= 1).all()
    concentration = impacts.concentration_share(result)
    assert not concentration["is_meaningful"].any()
    assert concentration["share_top_n"].dropna().eq(1.0).all()


def test_scenario_spread_reports_a_band(chain) -> None:
    """The spread between power estimates is an output, not an error."""
    _, result = chain
    spread = impacts.scenario_spread(result)
    assert (spread["n_scenarios"] > 1).all()
    assert (spread["delta_e_mt_max"] >= spread["delta_e_mt_min"]).all()


def test_domestic_threshold_matches_the_paper() -> None:
    assert DOMESTIC_THRESHOLD == 0.95
