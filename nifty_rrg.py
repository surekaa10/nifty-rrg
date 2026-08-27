"""Nifty Relative Rotation Graph (RRG).

    streamlit run "nifty_rrg.py"     # interactive app  (or double-click run.bat)
    python nifty_rrg.py --test       # self-check, no network

Data: Yahoo Finance via yfinance (auto-adjusted closes).
Sector membership lives in sectors.py.
"""

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from sectors import (BASKET_SECTORS, CONSTITUENTS, SECTOR_INDEX, SECTORS)

BENCHMARK = "^NSEI"          # Nifty 50

# Nifty 50 grouped by sector, for the single-stock mode.
NIFTY50_SECTORS = {
    "Banks": ["HDFCBANK", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "INDUSINDBK"],
    "Financials (non-bank)": ["BAJFINANCE", "BAJAJFINSV", "SHRIRAMFIN", "JIOFIN",
                              "SBILIFE", "HDFCLIFE"],
    "IT": ["TCS", "INFY", "HCLTECH", "WIPRO", "TECHM"],
    "Auto": ["MARUTI", "M&M", "TMPV", "BAJAJ-AUTO", "EICHERMOT", "HEROMOTOCO"],
    "FMCG": ["HINDUNILVR", "ITC", "NESTLEIND", "TATACONSUM"],
    "Pharma & Healthcare": ["SUNPHARMA", "DRREDDY", "CIPLA", "APOLLOHOSP"],
    "Metals": ["TATASTEEL", "JSWSTEEL", "HINDALCO"],
    "Energy": ["RELIANCE", "ONGC", "COALINDIA"],
    "Utilities": ["NTPC", "POWERGRID"],
    "Materials": ["ULTRACEMCO", "GRASIM", "ASIANPAINT"],
    "Consumer & Retail": ["TITAN", "TRENT", "ETERNAL"],
    "Capital Goods & Infra": ["LT", "BEL", "ADANIENT", "ADANIPORTS"],
    "Telecom": ["BHARTIARTL"],
}
NIFTY50 = [t for v in NIFTY50_SECTORS.values() for t in v]
SECTOR_OF = {t: s for s, v in NIFTY50_SECTORS.items() for t in v}

QUADRANTS = [  # x0, y0, x1, y1, colour, label
    (100, 100, 200, 200, "#16a34a", "LEADING"),
    (100, 0, 200, 100, "#d97706", "WEAKENING"),
    (0, 0, 100, 100, "#dc2626", "LAGGING"),
    (0, 100, 100, 200, "#2563eb", "IMPROVING"),
]
QUAD_COLOUR = {q[5]: q[4] for q in QUADRANTS}
QUAD_TINT = {"LEADING": "#dcfce7", "WEAKENING": "#fef3c7",
             "LAGGING": "#fee2e2", "IMPROVING": "#dbeafe"}
QUAD_ORDER = {"LEADING": 0, "IMPROVING": 1, "WEAKENING": 2, "LAGGING": 3}

DEFAULT_WINDOW = {"1d": 60, "1wk": 14, "1mo": 10}
DEFAULT_TAIL = {"1d": 12, "1wk": 8, "1mo": 6}
BAR_LABEL = {"1d": "Daily", "1wk": "Weekly", "1mo": "Monthly"}


# ---------------------------------------------------------------- maths

def rrg_coords(prices: pd.DataFrame, bench: pd.Series, window=14, mom=4, smooth=1):
    """JdK RS-Ratio / RS-Momentum, both normalised to mean 100.

    `smooth` is an EMA span applied to both coordinates — the raw weekly
    z-scores zig-zag badly, and every commercial RRG smooths before drawing.
    1 disables it.

    ponytail: time-series z-score per name — the standard public reproduction
    of the JdK formula. True RRG normalises cross-sectionally against the
    universe, which spreads names further apart. Swap the mean/std axis if the
    cloud ever looks too tight.
    """
    rs = 100 * prices.div(bench, axis=0)
    rs_ratio = 100 + (rs - rs.rolling(window).mean()) / rs.rolling(window).std()
    roc = 100 * rs_ratio / rs_ratio.shift(mom)
    rs_mom = 100 + (roc - roc.rolling(window).mean()) / roc.rolling(window).std()
    if smooth > 1:
        rs_ratio = rs_ratio.ewm(span=smooth).mean()
        rs_mom = rs_mom.ewm(span=smooth).mean()
    return rs_ratio, rs_mom


def quadrant(x, y):
    return ("LEADING" if y >= 100 else "WEAKENING") if x >= 100 else \
           ("IMPROVING" if y >= 100 else "LAGGING")


def basket(prices: pd.DataFrame) -> pd.Series:
    """Equal-weight index of the columns, rebased to 100 at the first bar."""
    return 100 * prices.div(prices.iloc[0]).mean(axis=1)


# ---------------------------------------------------------------- data

def nse(t: str) -> str:
    return t if "." in t or t.startswith("^") else f"{t}.NS"


def strip(c: str) -> str:
    return c[:-3] if c.endswith(".NS") else c


@st.cache_data(ttl=3600, show_spinner="Fetching from Yahoo Finance…")
def fetch(symbols: tuple, benchmark: str, period: str, interval: str):
    """Auto-adjusted closes, indexed on the benchmark's trading days."""
    import yfinance as yf

    want = sorted({nse(s) for s in symbols} | {nse(benchmark)})
    raw = yf.download(want, period=period, interval=interval,
                      auto_adjust=True, progress=False)["Close"]
    if isinstance(raw, pd.Series):
        raw = raw.to_frame(want[0])
    raw = raw.dropna(axis=1, how="all")
    dropped = [s for s in want if s not in raw.columns]
    bench = raw[nse(benchmark)].dropna()
    px = raw.drop(columns=[nse(benchmark)], errors="ignore").loc[bench.index].ffill()
    return px, bench, dropped


def sector_symbols(picked):
    """Yahoo symbols needed to build the chosen sectors."""
    out = []
    for k in picked:
        out += [SECTOR_INDEX[k]] if k in SECTOR_INDEX else CONSTITUENTS[k]
    return out


def sector_series(picked, px):
    """One column per chosen sector — index level, or equal-weight basket."""
    out = {}
    for k in picked:
        if k in SECTOR_INDEX:
            col = SECTOR_INDEX[k]
            if col in px.columns and px[col].notna().any():
                out[k] = px[col]
        else:
            cols = [nse(t) for t in CONSTITUENTS[k] if nse(t) in px.columns]
            if cols:
                out[k] = basket(px[cols].dropna())
    return pd.DataFrame(out)


# ---------------------------------------------------------------- plot

def plot(rs_ratio, rs_mom, tail, title, height=780):
    tails = {}
    for name in rs_ratio.columns:
        t = pd.DataFrame({"x": rs_ratio[name], "y": rs_mom[name]}).dropna().tail(tail)
        if len(t) >= 2:
            tails[name] = t
    if not tails:
        return None

    pad = 0.5
    vals = np.concatenate([t[["x", "y"]].values.ravel() for t in tails.values()])
    rng = [vals.min() - pad, vals.max() + pad]

    fig = go.Figure()
    for x0, y0, x1, y1, colour, label in QUADRANTS:
        fig.add_shape(type="rect", x0=x0, y0=y0, x1=x1, y1=y1, layer="below",
                      fillcolor=colour, opacity=0.06, line_width=0)
        fig.add_annotation(
            x=rng[0] if x0 < 100 else rng[1], y=rng[1] if y0 >= 100 else rng[0],
            text=f"<b>{label}</b>", showarrow=False,
            font=dict(size=15, color=colour), opacity=0.75,
            xanchor="left" if x0 < 100 else "right",
            yanchor="top" if y0 >= 100 else "bottom",
        )

    for name, t in tails.items():
        colour = QUAD_COLOUR[quadrant(t.x.iloc[-1], t.y.iloc[-1])]
        n = len(t) - 1
        # fading tail: oldest segment 20% opaque, newest fully opaque
        for i in range(n):
            f = (i + 1) / n
            fig.add_trace(go.Scatter(
                x=t.x.values[i:i + 2], y=t.y.values[i:i + 2], mode="lines",
                line=dict(color=colour, width=1 + 2 * f, shape="spline"),
                opacity=0.15 + 0.85 * f, showlegend=False, hoverinfo="skip",
            ))
        fig.add_trace(go.Scatter(
            x=t.x, y=t.y, name=name, mode="markers",
            marker=dict(size=[6] * n + [15], color=colour,
                        opacity=[0.3 + 0.7 * (i + 1) / n for i in range(n)] + [1],
                        line=dict(width=1.5, color="white")),
            customdata=[[d.strftime("%d %b %Y"), name] for d in t.index],
            hovertemplate="<b>%{customdata[1]}</b><br>%{customdata[0]}"
                          "<br>RS-Ratio %{x:.2f}<br>RS-Mom %{y:.2f}<extra></extra>",
        ))
        fig.add_annotation(x=t.x.iloc[-1], y=t.y.iloc[-1], text=f"  <b>{name}</b>",
                           showarrow=False, xanchor="left", yanchor="middle",
                           font=dict(size=11, color=colour))

    fig.add_hline(y=100, line=dict(color="#94a3b8", width=1))
    fig.add_vline(x=100, line=dict(color="#94a3b8", width=1))
    fig.update_layout(
        title=dict(text=title, font=dict(size=15)),
        template="plotly_white", height=height,
        xaxis=dict(title="JdK RS-Ratio  →  relative strength", range=rng,
                   constrain="domain", gridcolor="#eef2f7", zeroline=False),
        yaxis=dict(title="JdK RS-Momentum  →", range=rng, scaleanchor="x",
                   gridcolor="#eef2f7", zeroline=False),
        legend=dict(font=dict(size=10)), hovermode="closest",
        plot_bgcolor="white", margin=dict(l=60, r=90, t=60, b=55),
        clickmode="event+select",
    )
    return fig


def quadrant_table(rs_ratio, rs_mom, extra=None):
    t = pd.DataFrame({
        "RS-Ratio": rs_ratio.iloc[-1].round(2),
        "RS-Momentum": rs_mom.iloc[-1].round(2),
        "Quadrant": [quadrant(x, y) for x, y in zip(rs_ratio.iloc[-1], rs_mom.iloc[-1])],
    })
    if extra is not None:
        t.insert(0, extra.name, extra)
    t = t.sort_values(["Quadrant", "RS-Ratio"],
                      key=lambda c: c.map(QUAD_ORDER) if c.name == "Quadrant" else -c)
    return t


def show_table(t):
    """Rows tinted by quadrant so the four groups read at a glance."""
    styled = t.style.apply(
        lambda r: [f"background-color:{QUAD_TINT[r.Quadrant]};"
                   f"color:{QUAD_COLOUR[r.Quadrant]};font-weight:600"] * len(r),
        axis=1,
    ).format({"RS-Ratio": "{:.2f}", "RS-Momentum": "{:.2f}"})
    st.dataframe(styled, width="stretch", height=min(60 + 35 * len(t), 620))


def clicked_name(event):
    """Name of the point the user clicked, or None."""
    try:
        pts = event["selection"]["points"]
    except (TypeError, KeyError):
        return None
    for p in pts:
        cd = p.get("customdata")
        if cd:
            return cd[1]
    return None


# ---------------------------------------------------------------- app

def app():
    st.set_page_config("Nifty RRG", "🌀", layout="wide")
    st.title("Nifty Relative Rotation Graph")

    s = st.sidebar
    s.header("Inputs")
    mode = s.radio("Universe", ["Sectors", "Nifty 50 stocks", "Custom"])

    if mode == "Sectors":
        picked = s.multiselect("Sectors", SECTORS, default=SECTORS)
        symbols = sector_symbols(picked)
    elif mode == "Nifty 50 stocks":
        groups = s.multiselect("Filter by sector", list(NIFTY50_SECTORS),
                               default=list(NIFTY50_SECTORS))
        symbols = [t for g in groups for t in NIFTY50_SECTORS[g]]
    else:
        symbols = s.text_area("Tickers (space/comma separated)",
                              "RELIANCE TCS HDFCBANK INFY").replace(",", " ").split()

    benchmark = s.text_input("Benchmark", BENCHMARK,
                             help="^NSEI Nifty 50 · ^NSEBANK Bank Nifty · ^BSESN Sensex")
    interval = s.radio("Timeframe", ["1d", "1wk", "1mo"], index=1,
                       format_func=BAR_LABEL.get, horizontal=True)
    tail = s.select_slider("Tail length", [4, 6, 8, 12, 16],
                           value=DEFAULT_TAIL[interval])
    smooth = s.slider("Smoothing", 1, 10, 4,
                      help="EMA span on the plotted coordinates. Higher = smoother "
                           "tails, slower to react. 1 = raw.")
    period = s.selectbox("History", ["1y", "2y", "3y", "5y", "10y", "max"], index=2)

    with s.expander("Advanced"):
        window = st.slider("RRG lookback window", 5, 120, DEFAULT_WINDOW[interval],
                           help="Bars used to z-score relative strength. "
                                "14 weekly is the classic setting.")
        mom = st.slider("Momentum lookback", 1, 20, 4)
        top = st.slider("Show only N furthest from centre (0 = all)", 0, 50, 0)

    if not symbols:
        return st.info("Pick at least one sector or ticker.")

    px, bench, dropped = fetch(tuple(symbols), benchmark, period, interval)
    if dropped:
        s.warning("No Yahoo data: " + ", ".join(dropped))
    if px.empty:
        return st.error("No price data returned.")

    if mode == "Sectors":
        px = sector_series(picked, px)
    else:
        px.columns = [strip(c) for c in px.columns]
    if px.empty:
        return st.error("Nothing resolved to a price series.")

    rs_ratio, rs_mom = rrg_coords(px, bench, window, mom, smooth)
    if top:
        dist = np.hypot(rs_ratio.iloc[-1] - 100, rs_mom.iloc[-1] - 100)
        keep = dist.dropna().nlargest(top).index
        rs_ratio, rs_mom = rs_ratio[keep], rs_mom[keep]

    fig = plot(rs_ratio, rs_mom, tail,
               f"{len(rs_ratio.columns)} {'sectors' if mode == 'Sectors' else 'names'} "
               f"vs {benchmark} — {BAR_LABEL[interval]}, {tail}-bar tail, "
               f"as of {px.index[-1]:%d %b %Y}")
    if fig is None:
        return st.error(f"Not enough history for a {window}-bar window. "
                        "Increase History or cut the lookback.")

    if mode == "Sectors":
        st.caption("Click any dot to drill into that sector's stocks.")
    event = st.plotly_chart(fig, width="stretch", key="main_rrg", on_select="rerun")

    extra = None
    if mode == "Nifty 50 stocks":
        extra = pd.Series([SECTOR_OF.get(i, "—") for i in rs_ratio.columns],
                          index=rs_ratio.columns, name="Sector")
    elif mode == "Sectors":
        extra = pd.Series(["NSE index" if i in SECTOR_INDEX else "basket"
                           for i in rs_ratio.columns],
                          index=rs_ratio.columns, name="Source")
    table = quadrant_table(rs_ratio, rs_mom, extra)
    show_table(table)
    st.download_button("Download CSV", table.to_csv().encode(),
                       f"rrg_{interval}_{px.index[-1]:%Y%m%d}.csv", "text/csv")
    st.caption(
        "Dots rotate clockwise: Improving → Leading → Weakening → Lagging. "
        "Position is strength **relative to the benchmark**, not absolute return. "
        "Source: Yahoo Finance, auto-adjusted closes. Sectors marked *basket* are "
        "equal-weight constituent baskets — Yahoo has no history for those indices."
    )

    if mode == "Sectors":
        drilldown(clicked_name(event), picked, benchmark, period, interval,
                  tail, window, mom, smooth)


def drilldown(clicked, picked, benchmark, period, interval, tail, window, mom, smooth):
    st.divider()
    options = [s for s in picked if s in CONSTITUENTS]
    if not options:
        return
    idx = options.index(clicked) if clicked in options else 0
    sector = st.selectbox("Drill into sector", options, index=idx,
                          help="Or click a dot on the chart above.")
    against = st.radio("Benchmark the stocks against",
                       ["Nifty 50", f"the {sector} sector itself"],
                       horizontal=True)

    members = CONSTITUENTS[sector]
    bm = benchmark
    if against.startswith("the"):
        bm = SECTOR_INDEX.get(sector)
        if bm is None:                     # basket sector — no index to fetch
            bm = benchmark
            st.info(f"{sector} has no Yahoo index, so its stocks are shown "
                    "against Nifty 50.")

    px, bench, dropped = fetch(tuple(members), bm, period, interval)
    if dropped:
        st.warning("No Yahoo data: " + ", ".join(dropped))
    if px.empty:
        return st.error("No price data for those constituents.")
    px.columns = [strip(c) for c in px.columns]

    r, m = rrg_coords(px, bench, window, mom, smooth)
    fig = plot(r, m, tail,
               f"{sector} — {len(r.columns)} stocks vs {bm}, {BAR_LABEL[interval]}, "
               f"{tail}-bar tail, as of {px.index[-1]:%d %b %Y}", height=700)
    if fig is None:
        return st.error("Not enough history for these constituents.")
    st.plotly_chart(fig, width="stretch", key=f"drill_{sector}")
    t = quadrant_table(r, m)
    show_table(t)
    st.download_button("Download CSV", t.to_csv().encode(),
                       f"rrg_{sector.replace(' ', '_')}_{interval}.csv", "text/csv",
                       key="dl_drill")


# ---------------------------------------------------------------- check

def selftest():
    idx = pd.date_range("2020-01-01", periods=120, freq="W")
    bench = pd.Series(100 * 1.001 ** np.arange(120), index=idx)
    px = pd.DataFrame({"WINNER": bench * 1.004 ** np.arange(120),
                       "LOSER": bench * 0.996 ** np.arange(120)}, index=idx)
    r, m = rrg_coords(px, bench)
    assert r["WINNER"].iloc[-1] > 100 > r["LOSER"].iloc[-1], "RS-Ratio side wrong"
    assert quadrant(101, 101) == "LEADING" and quadrant(99, 101) == "IMPROVING"
    assert quadrant(101, 99) == "WEAKENING" and quadrant(99, 99) == "LAGGING"

    # smoothing must reduce jitter without moving the series off its own range
    noisy = px * (1 + 0.02 * np.tile([1, -1], (120, 1)))
    raw, _ = rrg_coords(noisy, bench, smooth=1)
    sm, _ = rrg_coords(noisy, bench, smooth=5)
    jit = lambda d: d["WINNER"].diff().abs().mean()
    assert jit(sm) < jit(raw), "smoothing did not smooth"

    b = basket(pd.DataFrame({"A": [10.0, 20.0], "B": [50.0, 50.0]}))
    assert b.iloc[0] == 100 and b.iloc[1] == 150, "basket rebasing wrong"

    assert len(SECTORS) == 24 == len(set(SECTORS)), f"{len(SECTORS)} sectors, want 24"
    assert not (set(BASKET_SECTORS) & set(SECTOR_INDEX)), "sector defined twice"
    assert len(BASKET_SECTORS) == 6, BASKET_SECTORS
    assert all(CONSTITUENTS[s] for s in SECTORS), "a sector has no constituents"
    assert len(NIFTY50) == len(set(NIFTY50)) == 50, f"universe is {len(NIFTY50)} names"

    # sector_series must handle both index-backed and basket-backed sectors
    days = pd.date_range("2024-01-01", periods=3)
    fake = pd.DataFrame({"^CNXIT": [1.0, 2, 3], "HAL.NS": [10.0, 20, 30],
                         "BEL.NS": [5.0, 5, 5]}, index=days)
    out = sector_series(["IT", "DEFENCE"], fake)
    assert list(out.columns) == ["IT", "DEFENCE"], out.columns
    assert out["IT"].iloc[-1] == 3 and out["DEFENCE"].iloc[0] == 100

    # table sorts Leading first, Lagging last, and strongest first within a group
    r2 = pd.DataFrame({"A": [101.0], "B": [102.0], "C": [98.0]})
    m2 = pd.DataFrame({"A": [101.0], "B": [101.0], "C": [98.0]})
    t = quadrant_table(r2, m2)
    assert list(t.index) == ["B", "A", "C"], list(t.index)
    assert list(t.Quadrant) == ["LEADING", "LEADING", "LAGGING"]

    assert clicked_name({"selection": {"points": [{"customdata": ["1 Jan", "IT"]}]}}) == "IT"
    assert clicked_name({"selection": {"points": []}}) is None
    assert clicked_name(None) is None
    print("ok")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        selftest()
    elif st.runtime.exists():
        app()
    else:
        print(__doc__)
