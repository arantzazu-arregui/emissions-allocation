"""§6 -- Global Carbon Budget baselines, unit conversion, Hong Kong treatments.

Establishes the national baseline against which allocated emissions are measured.

Two things the GCB layout makes easy to get wrong: the sheet reports million tonnes
of **carbon**, not CO2 (multiply by 3.664), and the header sits at row index 11 rather
than row 0. National columns already exclude bunker fuels, so the denominator is clean
and adding shipping emissions does not double-count.

Hong Kong is computed **both** ways. It is not a separate UNFCCC party, so the paper's
own alignment rule would fold it into China -- but the baselines differ by a factor of
~370, and for vessel A that choice decides whether a flag-versus-owner divergence
exists at all. The gap is presented as a methodological finding rather than buried in
an assumption.
"""

from __future__ import annotations

# Sheet 'Territorial Emissions', header at row index 11 (0-based). Wide layout:
# rows are years 1850-2024, columns are 232 countries. Units are MtC.
GCB_SHEET = "Territorial Emissions"
GCB_HEADER_ROW = 11


def load_gcb(*args, **kwargs):
    """Read the Territorial Emissions sheet and convert MtC to MtCO2."""
    raise NotImplementedError("baselines.load_gcb is not yet implemented.")


def national_baseline(*args, **kwargs):
    """``B_c`` in Mt CO2 for one country and year."""
    raise NotImplementedError("baselines.national_baseline is not yet implemented.")


def apply_hk_treatment(*args, **kwargs):
    """Keep Hong Kong separate, or fold it into China (§6.4)."""
    raise NotImplementedError("baselines.apply_hk_treatment is not yet implemented.")
