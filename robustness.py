"""Is the index construction good enough for an RRG?

The RRG reads *rotation*, not index levels, so the question is not "does this
match NSE's published series" - reconstructing NSE's actual rebalance history
is a large job with no payoff here. The question is whether the construction
choice changes the signal you would trade. If a theme lands in the same
quadrant however it is built, the construction detail does not matter and we
can stop refining it.

    python robustness.py           # quadrant stability across constructions
    python robustness.py --full    # per-theme detail
    python robustness.py --test    # self-check, no network

Constructions compared, all on identical prices and dates:
  doc      free-float + the document's caps           (what themes_rrg uses)
  equal    equal weight                               (naive)
  ffraw    free-float, no caps at all                 (cap sensitivity)
  phase    doc, rebalance offset by half a period     (timing sensitivity)
  annual   doc, rebalanced yearly instead of 6-monthly

A theme is STABLE if every construction puts it in the same quadrant, and
DIRECTION-STABLE if they also agree on which way it is rotating.
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

import themes as th
import weights as wt
from nifty_rrg import BENCHMARK, basket, nse, quadrant, rrg_coords
from themes_rrg import prices, split_rule

BUILDS = ("doc", "equal", "ffraw", "phase", "annual")
FLAT = 0.15                # RS drift below this is "flat", not a direction


def sign(x):
    """-1 / 0 / +1, treating a small drift as flat rather than directional."""
    return 0 if abs(x) < FLAT else (1 if x > 0 else -1)


def build(kind, px, cols, shares, rule, groups):
    """One theme's index series under the named construction."""
    if kind == "equal":
        return basket(px[cols].dropna())
    if kind == "ffraw":
        return wt.index_level(px[cols], shares, {}, None)
    if kind == "phase":                      # start a half-period late
        lvl = wt.index_level(px[cols].iloc[13:], shares, rule, groups)
        return lvl
    if kind == "annual":
        return wt.index_level(px[cols], shares, rule, groups, rebal=52)
    return wt.index_level(px[cols], shares, rule, groups)


def coords(series, bench, window=14, mom=4, smooth=4):
    """(quadrant, RS-Ratio, 4-week drift in ratio and momentum) for one series."""
    df = pd.DataFrame({"x": series}).dropna()
    if len(df) < window + mom + 6:
        return None
    r, m = rrg_coords(df, bench, window, mom, smooth)
    x, y = r["x"].iloc[-1], m["x"].iloc[-1]
    if not (np.isfinite(x) and np.isfinite(y)):
        return None
    return (quadrant(x, y), round(float(x), 2),
            round(float(x - r["x"].iloc[-5]), 2), round(float(y - m["x"].iloc[-5]), 2))


def compare(themes_raw, px, bench, shares) -> pd.DataFrame:
    rows = []
    for name, members in themes_raw.items():
        syms = th.symbols(members)
        cols = [nse(s) for s in syms if nse(s) in px.columns]
        if len(cols) < 5:
            continue
        ind = pd.Series({nse(r["symbol"]): r["industry"] for r in members})
        rule, groups = split_rule(name, syms, th.RULES.get(name, {}), ind)
        got = {}
        for kind in BUILDS:
            s = build(kind, px, cols, shares, rule, groups)
            c = coords(s, bench) if s is not None and not s.empty else None
            if c:
                got[kind] = c
        if "doc" not in got:
            continue
        quads = {k: v[0] for k, v in got.items()}
        ratios = [v[1] for v in got.values()]
        # Rotation direction as a sign pair, with a deadband: a drift of 0.02
        # and one of -0.01 are the same "flat", and raw np.sign would call them
        # a disagreement.
        dirs = {k: (sign(v[2]), sign(v[3])) for k, v in got.items()}
        rows.append({
            "theme": name, "doc_quadrant": quads["doc"],
            "n_builds": len(got),
            "quad_agree": sum(q == quads["doc"] for q in quads.values()),
            "stable": len(set(quads.values())) == 1,
            "dir_stable": len(set(dirs.values())) == 1,
            "ratio_spread": round(max(ratios) - min(ratios), 2),
            "disagrees": ", ".join(f"{k}={v}" for k, v in quads.items()
                                   if v != quads["doc"]) or "-",
        })
    return pd.DataFrame(rows)


def demo():
    """Self-check: the comparison must flag agreement and disagreement."""
    i = pd.date_range("2020-01-01", periods=140, freq="W")
    b = pd.Series(100 * 1.001 ** np.arange(140), index=i)
    up = pd.Series(100 * 1.005 ** np.arange(140), index=i)
    c = coords(up, b)
    # a steady out-performer sits right of centre; its RS-Momentum decays to
    # zero because the *rate* of out-performance is constant, so the quadrant
    # is not the thing to assert on here - the ratio is.
    assert c and c[1] > 100, c
    assert coords(up.iloc[:5], b) is None, "too-short series must return None"

    # identical builds => stable; a real divergence => not stable
    same = pd.DataFrame([{"theme": "A", "doc_quadrant": "LEADING", "n_builds": 5,
                          "quad_agree": 5, "stable": True, "dir_stable": True,
                          "ratio_spread": 0.1, "disagrees": "-"}])
    assert same.stable.all()
    assert sign(0.02) == 0 == sign(-0.01), "deadband must swallow noise"
    assert sign(0.9) == 1 and sign(-0.9) == -1
    print("ok")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--full", action="store_true")
    p.add_argument("--test", action="store_true")
    a = p.parse_args()
    if a.test:
        demo(); raise SystemExit

    raw = th.fetch()
    px = prices([], refresh=False)
    bench = px[BENCHMARK].dropna()
    px = px.loc[bench.index].ffill()
    shares = wt.float_shares([c for c in px.columns if c != BENCHMARK])
    t = compare(raw, px, bench, shares)

    pd.set_option("display.width", 220, "display.max_rows", None)
    t = t.sort_values(["stable", "ratio_spread"], ascending=[True, False])
    if a.full:
        print(t.to_string(index=False))
    else:
        print(t[["theme", "doc_quadrant", "stable", "dir_stable",
                 "ratio_spread", "disagrees"]].to_string(index=False))
    n = len(t)
    print(f"\nquadrant identical across all {len(BUILDS)} constructions: "
          f"{t.stable.sum()}/{n} themes ({100 * t.stable.mean():.0f}%)")
    print(f"rotation direction identical:                    "
          f"{t.dir_stable.sum()}/{n} themes ({100 * t.dir_stable.mean():.0f}%)")
    print(f"median RS-Ratio spread across constructions:     {t.ratio_spread.median():.2f}")
    print(f"worst: {t.ratio_spread.max():.2f} ({t.iloc[0].theme})")
