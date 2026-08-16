"""§7 -- absolute and relative additions to national carbon budgets.

Expresses allocated emissions as ``dE`` and ``dE%`` against each baseline, ranks
them, and reports concentration shares.

Rankings and concentration shares are structurally meaningless at n=2 -- the code path
exists and is exercised, but the interpretable outputs at this scale are annual and
total CO2 under each power estimate, the same figure attributed to Hong Kong versus
China under the flag and owner options, dE% against each candidate baseline (which is
where the ~370x divergence becomes visible), and the spread across scenarios as an
explicit uncertainty band.
"""

from __future__ import annotations


def compute_impacts(*args, **kwargs):
    """``dE_c = E_c`` and ``dE%_c = 100 * dE_c / B_c`` (§7.1)."""
    raise NotImplementedError("impacts.compute_impacts is not yet implemented.")


def rank_countries(*args, **kwargs):
    """``RANK() OVER (PARTITION BY option, scenario ORDER BY dE_c DESC)`` (§7.1)."""
    raise NotImplementedError("impacts.rank_countries is not yet implemented.")


def concentration_share(*args, **kwargs):
    """Top-20 share of total allocated emissions (§7.1)."""
    raise NotImplementedError("impacts.concentration_share is not yet implemented.")
