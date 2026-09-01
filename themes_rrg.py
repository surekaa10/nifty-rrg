"""Thematic RRG, and the stocks carrying both a sector and a theme tailwind.

    python themes_rrg.py                 # theme table + cross-tailwind stocks
    python themes_rrg.py --quad LEADING  # only stocks under leading themes
    python themes_rrg.py --refresh       # re-pull prices and NSE membership
    python themes_rrg.py --test          # self-check, no network

A stock is flagged when three things line up on the weekly RRG: its sector is
right-of-centre or turning up, at least one of its themes is too, and the stock
itself is. Sector answers "is the money here", theme answers "is the story
here", the stock answers "is this the name doing the work".

Themes are rebuilt the way NSE builds them: free-float market-cap weighted,
capped per `themes.RULES`, rebalanced semi-annually (see weights.py, and the
limits documented there). The six sector indices Yahoo has no history for are
rebuilt the same way, under the sectoral standard of a 33% stock cap and a 62%
cumulative cap on the top 3.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import themes as th
import weights as wt
from nifty_rrg import BENCHMARK, nse, quadrant, rrg_coords, strip
from sectors import CONSTITUENTS, SECTOR_INDEX, SECTORS

HERE = Path(__file__).parent
PX_CACHE = HERE / "px_weekly.pkl"
STRONG = ("LEADING", "IMPROVING")
# "All sectoral indices are capped as per Index characteristics" - the document
# states 33% / top-3 62% explicitly for Nifty Healthcare; it is the sectoral norm.
SECTOR_RULE = {"stock_cap": 0.33, "top_n": 3, "top_cap": 0.62}
ORDER = {"LEADING": 0, "IMPROVING": 1, "WEAKENING": 2, "LAGGING": 3}


def prices(symbols, period="3y", interval="1wk", refresh=False) -> pd.DataFrame:
    """Weekly closes for the whole universe, cached for the day."""
    if PX_CACHE.exists() and not refresh:
        px = pd.read_pickle(PX_CACHE)
        if px.attrs.get("fetched") == date.today().isoformat():
            return px
    import yfinance as yf
    raw = yf.download(sorted(symbols), period=period, interval=interval,
                      auto_adjust=True, progress=False)["Close"]
    # Yahoo answers dead symbols with a single stub bar - drop those outright
    raw = raw[[c for c in raw.columns if raw[c].notna().sum() > 100]]
    raw.attrs["fetched"] = date.today().isoformat()
    raw.to_pickle(PX_CACHE)
    return raw


def split_rule(name, syms, rule, industry):
    """Resolve an index whose cap is not uniform into (rule, groups).

    Two indices cap on something the constituent CSV does not carry: Mobility
    on basic industry, Conglomerate 50 on business group. `themes` hand-maps
    both; everything else caps on the CSV's own Industry column.
    """
    kind = rule.get("split")
    if kind == "mobility":
        cap, basic, gcap = th.mobility_caps(syms)
        cap.index = [nse(i) for i in cap.index]
        basic.index = [nse(i) for i in basic.index]
        return {**rule, "stock_cap": cap, "sector_cap": gcap}, basic
    if kind == "conglomerate":
        grp = pd.Series({nse(x): th.CONGLOMERATE_GROUP.get(x, "Other")
                         for x in syms})
        return rule, grp
    return rule, industry


def series(members, px, shares, index_of=None, rules=None, industries=None):
    """One column per group: its real NSE index if Yahoo has one, else the
    index rebuilt from constituents the way the methodology document specifies.

    `members` is {name: [symbols]}, `rules` is {name: cap rule}, `industries`
    is {name: Series(symbol -> industry)} for the groups carrying a sector cap.
    """
    index_of, rules, industries = index_of or {}, rules or {}, industries or {}
    out, src = {}, {}
    for name, syms in members.items():
        idx = index_of.get(name)
        if idx and idx in px.columns:
            out[name], src[name] = px[idx], "NSE index"
            continue
        cols = [nse(s) for s in syms if nse(s) in px.columns]
        if len(cols) < 5:
            continue
        rule, ind = split_rule(name, syms, rules.get(name, SECTOR_RULE),
                               industries.get(name))
        lvl = wt.index_level(px[cols], shares, rule, ind)
        if not lvl.empty:
            out[name] = lvl
            cap = rule["stock_cap"]
            label = ("%.0f-%.0f%%" % (100 * cap.min(), 100 * cap.max())
                     if hasattr(cap, "min") else "%.1f%%" % (100 * cap))
            src[name] = "ff-cap %d@%s" % (len(cols), label)
    return pd.DataFrame(out), pd.Series(src, name="src")


def table(r, m, src=None, px=None):
    """Current coordinates, quadrant, and 4-week drift, sorted best first."""
    t = pd.DataFrame({
        "RS_Ratio": r.iloc[-1].round(2), "RS_Mom": m.iloc[-1].round(2),
        "Quadrant": [quadrant(x, y) for x, y in zip(r.iloc[-1], m.iloc[-1])],
        "d_ratio_4w": (r.iloc[-1] - r.iloc[-5]).round(2),
        "d_mom_4w": (m.iloc[-1] - m.iloc[-5]).round(2),
    }).dropna(subset=["RS_Ratio"])
    if px is not None:
        t["ret_4w%"] = (100 * (px.iloc[-1] / px.iloc[-5] - 1)).round(1)
        t["ret_13w%"] = (100 * (px.iloc[-1] / px.iloc[-14] - 1)).round(1)
    if src is not None:
        t.insert(0, "src", src)
    return t.sort_values(["Quadrant", "RS_Ratio"],
                         key=lambda c: c.map(ORDER) if c.name == "Quadrant" else -c)


def crosswind(stock_q, sector_of, theme_of, keep=STRONG):
    """Stocks whose own quadrant, sector quadrant and a theme quadrant all qualify.

    `stock_q` is {symbol: quadrant}; `sector_of`/`theme_of` map a symbol to the
    [(group, quadrant)] it belongs to. A stock needs at least one qualifying
    sector AND one qualifying theme - membership alone is not a tailwind.
    """
    rows = []
    for sym, q in stock_q.items():
        if q not in keep:
            continue
        secs = [g for g, gq in sector_of.get(sym, []) if gq in keep]
        thms = [g for g, gq in theme_of.get(sym, []) if gq in keep]
        if secs and thms:
            rows.append({"symbol": sym, "stock_q": q, "sectors": ", ".join(secs),
                         "themes": ", ".join(thms), "n_tail": len(secs) + len(thms)})
    return pd.DataFrame(rows)


def run(a):
    raw_themes = th.fetch(a.refresh)
    themes = {k: th.symbols(v) for k, v in raw_themes.items()}
    industries = {k: pd.Series({nse(r["symbol"]): r["industry"] for r in v})
                  for k, v in raw_themes.items()}
    sectors = {s: CONSTITUENTS[s] for s in SECTORS}
    universe = ({nse(s) for v in themes.values() for s in v}
                | {nse(s) for v in sectors.values() for s in v}
                | set(SECTOR_INDEX.values()) | {BENCHMARK})
    px = prices(universe, a.period, refresh=a.refresh)
    bench = px[BENCHMARK].dropna()
    px = px.loc[bench.index].ffill()

    stock_cols = [c for c in px.columns
                  if c != BENCHMARK and c not in set(SECTOR_INDEX.values())]
    shares = wt.float_shares(stock_cols, refresh=a.refresh)
    print("free-float shares resolved for %d/%d stocks"
          % (len(shares), len(stock_cols)))

    tpx, tsrc = series(themes, px, shares, rules=th.RULES, industries=industries)
    spx, ssrc = series(sectors, px, shares, SECTOR_INDEX)
    tr, tm = rrg_coords(tpx, bench, a.window, a.mom, a.smooth)
    sr, sm = rrg_coords(spx, bench, a.window, a.mom, a.smooth)

    stocks = px.drop(columns=[BENCHMARK], errors="ignore")
    stocks = stocks[[c for c in stocks.columns if c not in SECTOR_INDEX.values()]]
    kr, km = rrg_coords(stocks, bench, a.window, a.mom, a.smooth)

    tt = table(tr, tm, tsrc, tpx)
    print("\n=== NIFTY THEMATIC RRG - weekly vs %s, as of %s (last bar unclosed) ===\n"
          % (BENCHMARK, px.index[-1].strftime("%d %b %Y")))
    print(tt.to_string())

    tq = dict(zip(tt.index, tt.Quadrant))
    st = table(sr, sm, ssrc, spx)
    sq = dict(zip(st.index, st.Quadrant))

    stock_q, sector_of, theme_of = {}, {}, {}
    for c in kr.columns:
        x, y = kr[c].iloc[-1], km[c].iloc[-1]
        if np.isfinite(x) and np.isfinite(y):
            stock_q[strip(c)] = quadrant(x, y)
    for name, syms in sectors.items():
        for s in syms:
            sector_of.setdefault(s, []).append((name, sq.get(name, "")))
    for name, syms in themes.items():
        for s in syms:
            theme_of.setdefault(s, []).append((name, tq.get(name, "")))

    keep = (a.quad,) if a.quad else STRONG
    x = crosswind(stock_q, sector_of, theme_of, keep)
    if x.empty:
        print("\nNo stock currently has both a sector and a theme tailwind.")
        return
    x["RS_Ratio"] = [round(kr[nse(s)].iloc[-1], 2) for s in x.symbol]
    x["RS_Mom"] = [round(km[nse(s)].iloc[-1], 2) for s in x.symbol]
    x["ret_4w%"] = [round(100 * (px[nse(s)].iloc[-1] / px[nse(s)].iloc[-5] - 1), 1)
                    for s in x.symbol]
    x = x.sort_values(["stock_q", "n_tail", "RS_Ratio"],
                      key=lambda c: c.map(ORDER) if c.name == "stock_q" else -c)
    print("\n=== STOCKS WITH BOTH A SECTOR AND A THEME TAILWIND (%s) - %d names ===\n"
          % ("/".join(keep), len(x)))
    print(x.head(a.top).to_string(index=False))
    out = HERE / ("crosswind_%s.csv" % date.today())
    x.to_csv(out, index=False)
    print("\nfull list -> %s" % out.name)


def demo():
    """Self-check: the screen must demand all three legs, not any two."""
    sec = {"A": [("BANK", "LEADING")], "B": [("IT", "LAGGING")],
           "C": [("BANK", "LEADING")], "D": [("BANK", "LEADING")]}
    thm = {"A": [("DEFENCE", "IMPROVING")], "B": [("DEFENCE", "LEADING")],
           "C": [("DEFENCE", "WEAKENING")], "D": []}
    q = {"A": "LEADING", "B": "LEADING", "C": "LEADING", "D": "LEADING"}
    got = set(crosswind(q, sec, thm).symbol)
    assert got == {"A"}, got            # B fails sector, C fails theme, D has none
    assert crosswind({"A": "LAGGING"}, sec, thm).empty, "weak stock must not pass"
    # a stock in two strong themes ranks above one in a single theme
    thm2 = {"A": [("DEFENCE", "LEADING"), ("PSE", "LEADING")]}
    assert crosswind({"A": "LEADING"}, sec, thm2).n_tail.iloc[0] == 3

    idx = pd.date_range("2020-01-01", periods=120, freq="W")
    b = pd.Series(100 * 1.001 ** np.arange(120), index=idx)
    up = pd.DataFrame({"UP": b * 1.004 ** np.arange(120)}, index=idx)
    r, m = rrg_coords(up, b)
    assert table(r, m).Quadrant.iloc[0] in STRONG

    # Mobility's split cap must reach the builder as a per-name Series
    rule, grp = split_rule("MOBILITY", ["RELIANCE", "MARUTI"],
                           th.RULES["MOBILITY"], None)
    assert rule["stock_cap"]["RELIANCE.NS"] == 0.05, rule["stock_cap"]
    assert rule["stock_cap"]["MARUTI.NS"] == 0.08
    assert rule["sector_cap"]["Refineries & Marketing"] == 0.20
    assert grp["RELIANCE.NS"] == "Refineries & Marketing"
    # Conglomerate caps on business group, not the CSV industry column
    _, cg = split_rule("CONGLOMERATE 50", ["TCS", "ADANIENT"],
                       th.RULES["CONGLOMERATE 50"], None)
    assert cg["TCS.NS"] == "Tata" and cg["ADANIENT.NS"] == "Adani", cg
    # an ordinary index is passed through untouched
    r2, i2 = split_rule("WAVES", ["ZEEL"], th.RULES["WAVES"], "keepme")
    assert r2 is th.RULES["WAVES"] and i2 == "keepme"
    print("ok")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--quad", choices=list(ORDER), help="require this exact quadrant")
    p.add_argument("--period", default="3y")
    p.add_argument("--window", type=int, default=14)
    p.add_argument("--mom", type=int, default=4)
    p.add_argument("--smooth", type=int, default=4)
    p.add_argument("--top", type=int, default=40)
    p.add_argument("--refresh", action="store_true")
    p.add_argument("--test", action="store_true")
    a = p.parse_args()
    pd.set_option("display.width", 240, "display.max_rows", None)
    demo() if a.test else run(a)
