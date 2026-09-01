"""Free-float market-cap index construction, per the NSE methodology document.

NSE builds every thematic index the same way: weight each constituent by its
free-float market capitalisation, then cap - some stocks, some sectors, some
both - and rebalance periodically, letting weights drift with price in between.
`Method_NIFTY_Equity_Indices.pdf` (August 2026) is the source for the per-index
caps in `themes.RULES`.

    python weights.py --test     # self-check, no network

What this reproduces faithfully:
  * free-float market-cap weighting (float shares x price)
  * the documented per-stock cap, applied by NSE's iterative redistribution
  * documented sector caps and the PSE/Services top-3 cumulative cap
  * weights fixed at rebalance, drifting with price between rebalances

What it cannot reproduce, and why:
  * NSE weights off the *6-month average* free-float mcap at each review date.
    Yahoo publishes one current `floatShares` number, so today's float is used
    for the whole history.
  * NSE publishes historical constituents only as of today. Backfilling current
    membership over old prices is survivorship bias, and no free source fixes it.
  * NSE's own IWF is an exact published figure; `floatShares / sharesOutstanding`
    is Yahoo's estimate of the same thing.
So index *levels* here will not match NSE's published values. Relative rotation
- which quadrant a theme sits in, and which way it is travelling - is what these
series are for, and that is far less sensitive to the above than a level is.

ponytail: capping iterates to a fixed point with a 100-round ceiling rather
than solving the constrained allocation exactly. It converges in a handful of
rounds for every real index; the ceiling only exists so a pathological input
cannot spin forever.
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
FLOAT_CACHE = HERE / "float_cache.json"
STALE_DAYS = 30
REBAL_BARS = 26                        # semi-annual, on weekly bars


def cap_weights(w: pd.Series, stock_cap=None, groups=None, group_cap=None,
                top_n=None, top_cap=None, rounds=100) -> pd.Series:
    """Normalise to 1, then enforce the caps by NSE's redistribution rule.

    Excess weight above a cap is taken off the capped names and handed to the
    uncapped ones in proportion to what they already hold, repeatedly, until
    nothing breaches. `groups` maps each index label to its sector/group.
    """
    w = w[w > 0].astype(float)
    if w.empty:
        return w
    w = w / w.sum()
    # an infeasible cap (n * cap <= 1) has exactly one solution: equal weight.
    # Without this the redistribution loop just oscillates forever.
    if stock_cap is not None and len(w) * stock_cap <= 1 + 1e-12:
        return pd.Series(1.0 / len(w), index=w.index)

    for _ in range(rounds):
        moved = False
        if stock_cap is not None:
            over = w > stock_cap + 1e-12
            if over.any() and (~over).any():
                excess = float((w[over] - stock_cap).sum())
                w[over] = stock_cap
                free = w[~over]
                w[~over] = free + excess * free / free.sum()
                moved = True
        if groups is not None and group_cap is not None:
            g = w.groupby(groups).sum()
            hot = g[g > group_cap + 1e-12]
            if len(hot) and len(g) > len(hot):
                excess = float((hot - group_cap).sum())
                for name in hot.index:                 # scale the group to its cap
                    m = groups.reindex(w.index) == name
                    w[m] *= group_cap / g[name]
                cool = ~groups.reindex(w.index).isin(hot.index)
                if w[cool].sum() > 0:
                    w[cool] += excess * w[cool] / w[cool].sum()
                    moved = True
        # a top-N cumulative cap is only satisfiable when it exceeds the
        # equal-weight share of those N names; below that, nothing converges.
        if (top_n and top_cap is not None and len(w) > top_n
                and top_cap > top_n / len(w) + 1e-12):
            top = w.nlargest(top_n)
            if top.sum() > top_cap + 1e-12:
                excess = float(top.sum() - top_cap)
                w[top.index] *= top_cap / top.sum()
                rest = w.drop(top.index)
                w[rest.index] = rest + excess * rest / rest.sum()
                moved = True
        if not moved:
            break
    return w / w.sum()


def float_shares(symbols, refresh=False) -> dict:
    """{yahoo symbol: free-float share count}, cached - one HTTP call each."""
    cache = {}
    if FLOAT_CACHE.exists() and not refresh:
        blob = json.loads(FLOAT_CACHE.read_text())
        if (date.today() - date.fromisoformat(blob["fetched"])).days < STALE_DAYS:
            cache = blob["float"]
    missing = [s for s in symbols if s not in cache]
    if missing:
        import concurrent.futures as cf
        import yfinance as yf

        def one(s):
            try:
                i = yf.Ticker(s).info
                return s, (i.get("floatShares") or i.get("sharesOutstanding"))
            except Exception:
                return s, None
        with cf.ThreadPoolExecutor(8) as ex:
            for s, v in ex.map(one, missing):
                cache[s] = v
        FLOAT_CACHE.write_text(json.dumps(
            {"fetched": date.today().isoformat(), "float": cache}, indent=1))
    return {k: v for k, v in cache.items() if v}


def index_level(px: pd.DataFrame, shares: dict, rule: dict,
                industry=None, rebal=REBAL_BARS, base=100.0) -> pd.Series:
    """A capped free-float index over `px`, rebased to `base` at the first bar.

    Between rebalances the holding is fixed, so weights drift with price -
    the behaviour the document describes. At each rebalance the caps are
    re-applied to the free-float market caps of that day.
    """
    cols = [c for c in px.columns if shares.get(c)]
    if len(cols) < 5:
        return pd.Series(dtype=float)
    px = px[cols].dropna(how="all").ffill().dropna()
    if px.empty:
        return pd.Series(dtype=float)
    ff = pd.Series({c: float(shares[c]) for c in cols})
    groups = industry.reindex(cols) if industry is not None else None

    level, units, out = base, None, []
    for i, (_, row) in enumerate(px.iterrows()):
        if i % rebal == 0:
            w = cap_weights(ff * row, rule.get("stock_cap"), groups,
                            rule.get("sector_cap"), rule.get("top_n"),
                            rule.get("top_cap"))
            units = (w * level / row.reindex(w.index)).dropna()
        level = float((units * row.reindex(units.index)).sum())
        out.append(level)
    return pd.Series(out, index=px.index)


def demo():
    """Self-check: capping, redistribution, and index construction."""
    w = pd.Series({"A": 0.90, "B": 0.02, "C": 0.02, "D": 0.02, "E": 0.02, "F": 0.02})
    c = cap_weights(w, stock_cap=0.20)
    assert abs(c.sum() - 1) < 1e-9, c.sum()
    assert abs(c["A"] - 0.20) < 1e-9, c["A"]          # capped exactly
    assert abs(c["B"] - c["C"]) < 1e-9               # excess split pro-rata
    assert (c <= 0.20 + 1e-9).all(), c
    # an uncapped name lifted past the cap must be capped on the next round
    w2 = pd.Series({"A": 0.80, "B": 0.19, "C": 0.01})
    c2 = cap_weights(w2, stock_cap=0.5)
    assert (c2 <= 0.5 + 1e-9).all(), c2
    # an infeasible cap collapses to equal weight instead of oscillating
    c3 = cap_weights(pd.Series({"A": 0.9, "B": 0.05, "C": 0.05}), stock_cap=0.2)
    assert abs(c3.sum() - 1) < 1e-9 and c3.notna().all()
    assert (abs(c3 - 1 / 3) < 1e-9).all(), c3

    g = pd.Series({"A": "X", "B": "X", "C": "Y"})
    c4 = cap_weights(pd.Series({"A": 0.5, "B": 0.4, "C": 0.1}),
                     groups=g, group_cap=0.6)
    assert abs(c4.groupby(g).sum()["X"] - 0.6) < 1e-6, c4.groupby(g).sum()

    # PSE/Services style: top 3 of a wide index held to 62% cumulative
    wide = pd.Series({"A": .30, "B": .25, "C": .20} |
                     {c: .025 for c in "DEFGHIJK"})
    c5 = cap_weights(wide, top_n=3, top_cap=0.62)
    assert abs(c5.sum() - 1) < 1e-9
    assert abs(c5.nlargest(3).sum() - 0.62) < 1e-6, c5.nlargest(3).sum()
    # infeasible top-N cap is left alone rather than oscillating
    c6 = cap_weights(pd.Series({"A": .4, "B": .3, "C": .2, "D": .1}),
                     top_n=3, top_cap=0.62)
    assert abs(c6.sum() - 1) < 1e-9 and c6.notna().all()

    # a float-weighted index must track the big name, not the small one
    idx = pd.date_range("2024-01-01", periods=30, freq="W")
    px = pd.DataFrame({"BIG": np.linspace(100, 200, 30),
                       "SMALL": np.linspace(100, 50, 30),
                       "C": np.full(30, 100.0), "D": np.full(30, 100.0),
                       "E": np.full(30, 100.0)}, index=idx)
    sh = {"BIG": 1e9, "SMALL": 1e6, "C": 1e6, "D": 1e6, "E": 1e6}
    lv = index_level(px, sh, {"stock_cap": None})
    assert abs(lv.iloc[0] - 100) < 1e-9 and lv.iloc[-1] > 180, lv.iloc[-1]
    # the same index with a hard cap must lag it - BIG can only hold 20%
    lvc = index_level(px, sh, {"stock_cap": 0.20})
    assert lvc.iloc[-1] < lv.iloc[-1], (lvc.iloc[-1], lv.iloc[-1])
    assert index_level(px[["BIG"]], sh, {}).empty, "too few names must be empty"
    print("ok")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--test", action="store_true")
    if p.parse_args().test:
        demo()
    else:
        print(__doc__)
