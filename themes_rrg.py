"""Thematic RRG, and the stocks carrying both a sector and a theme tailwind.

    python themes_rrg.py                 # theme table + cross-tailwind stocks
    python themes_rrg.py --quad LEADING  # only stocks under leading themes
    python themes_rrg.py --refresh       # re-pull prices and NSE membership
    python themes_rrg.py --test          # self-check, no network

A stock is flagged when three things line up on the weekly RRG: its sector is
right-of-centre or turning up, at least one of its themes is too, and the stock
itself is. Sector answers "is the money here", theme answers "is the story
here", the stock answers "is this the name doing the work".

ponytail: themes are equal-weight baskets of NSE's published membership (see
themes.py) - no free-float weights, so a theme carried by one mega-cap reads
flatter here than its real index does.
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

import themes as th
from nifty_rrg import BENCHMARK, basket, nse, quadrant, rrg_coords, strip
from sectors import CONSTITUENTS, SECTOR_INDEX, SECTORS

HERE = Path(__file__).parent
PX_CACHE = HERE / "px_weekly.pkl"
STRONG = ("LEADING", "IMPROVING")
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


def series(members, px, index_of=None):
    """One column per group: its real index if Yahoo has one, else a basket."""
    index_of = index_of or {}
    out, src = {}, {}
    for name, syms in members.items():
        idx = index_of.get(name)
        if idx and idx in px.columns:
            out[name], src[name] = px[idx], "index"
            continue
        cols = [nse(s) for s in syms if nse(s) in px.columns]
        if len(cols) >= 5:
            b = px[cols].dropna()
            out[name], src[name] = basket(b), "basket(%d)" % len(cols)
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
    themes = th.fetch(a.refresh)
    sectors = {s: CONSTITUENTS[s] for s in SECTORS}
    universe = ({nse(s) for v in themes.values() for s in v}
                | {nse(s) for v in sectors.values() for s in v}
                | set(SECTOR_INDEX.values()) | {BENCHMARK})
    px = prices(universe, a.period, refresh=a.refresh)
    bench = px[BENCHMARK].dropna()
    px = px.loc[bench.index].ffill()

    tpx, tsrc = series(themes, px)
    spx, ssrc = series(sectors, px, SECTOR_INDEX)
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
