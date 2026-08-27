# Nifty RRG

Relative Rotation Graph for the 24 NSE sector/thematic indices and for Nifty 50
stocks, benchmarked against the Nifty 50.

## Run it

Double-click **`run.bat`**, or:

```
python -m streamlit run "nifty_rrg.py"
```

Opens at http://localhost:8501. Every input is a sidebar control and the chart
redraws live. Prices cache for an hour, so only the first load hits the network.

Offline sanity check: `python nifty_rrg.py --test`

## Inputs

| Control | Options |
|---|---|
| **Universe** | `Sectors` (the 24 below), `Nifty 50 stocks`, `Custom` (type any tickers) |
| **Sectors** | Multi-select — deselect to declutter |
| **Benchmark** | `^NSEI` (Nifty 50) by default; `^NSEBANK`, `^BSESN` etc. also work |
| **Timeframe** | Daily / Weekly / Monthly |
| **Tail length** | 4 / 6 / 8 / 12 / 16 bars |
| **History** | 1y – max |
| *Advanced* → RRG lookback | Bars used to normalise relative strength. 14 weekly is the classic |
| *Advanced* → Momentum lookback | Rate-of-change span for the vertical axis |
| *Advanced* → Show only N furthest | Keeps the N names furthest from centre |

Reading it: dots rotate **clockwise**, Improving → Leading → Weakening →
Lagging. Position is strength *relative to the benchmark*, not absolute return —
a sector can be in Lagging while still rising, if it's rising slower than Nifty.
Tails fade with age: the opaque segment nearest the dot is the most recent bar.

## The 24 sectors

**18 are the real NSE indices**, pulled directly:

| Sector | Ticker | Sector | Ticker |
|---|---|---|---|
| BANKNIFTY | `^NSEBANK` | INFRA | `^CNXINFRA` |
| SENSEX | `^BSESN` | IT | `^CNXIT` |
| FINNIFTY | `NIFTY_FIN_SERVICE.NS` | MEDIA | `^CNXMEDIA` |
| MIDCAP | `NIFTY_MIDCAP_100.NS` | METAL | `^CNXMETAL` |
| AUTO | `^CNXAUTO` | PHARMA | `^CNXPHARMA` |
| COMMODITIES | `^CNXCMDT` | PSU BANK | `^CNXPSUBANK` |
| CONSUMPTION | `^CNXCONSUM` | PVT BANK | `NIFTY_PVT_BANK.NS` |
| ENERGY | `^CNXENERGY` | REALTY | `^CNXREALTY` |
| FMCG | `^CNXFMCG` | SERVICES | `^CNXSERVICE` |

**6 are equal-weight constituent baskets** — `CAPITAL MRKT`, `CHEMICALS`,
`CONSR DURBL`, `DEFENCE`, `HEALTHCARE`, `OIL & GAS`. Yahoo resolves these
index symbols but serves a live quote with *zero* history, and NSE's own API
returns 403 to server-side requests. So the app rebuilds them from their
constituents (listed in `CONSTITUENTS` in `sectors.py`), rebased to 100 at
the start of the window. The table marks each row `NSE index` or `basket`.

Caveat on the baskets: membership is hand-listed and NSE rebalances, so index
*levels* won't match NSE's published values, and equal-weight differs from the
free-float weighting NSE uses. Rotation direction and quadrant are what the
chart is for, and those hold. Edit `CONSTITUENTS` in `sectors.py` to correct membership.

Three Oil & Gas names (`GSPL`, `GUJGASLTD`, `ADANITOTAL` under that symbol)
have no usable Yahoo history and are left out of that basket.

## Where the data comes from

Yahoo Finance via the `yfinance` package — NSE equities as `SYMBOL.NS`,
auto-adjusted closes (splits and dividends applied). No API key. It's a free
scraped feed: research-grade, not execution-grade.

Known Yahoo quirks handled in the code:

- **Tata Motors** — post-demerger only `TMPV.NS` (passenger vehicles) carries
  history; the commercial-vehicle entity has no working symbol.
- The six historyless sector indices above.
- Scattered missing days on the sector indices (~470 of 495 trading days over
  two years) are forward-filled onto the benchmark's calendar.

Anything that fails to download is listed as a sidebar warning, not silently
dropped.

## Thematic RRG (`themes_rrg.py`)

The sector RRG asks *where is the money*. The thematic one asks *where is the
story* — 24 Nifty thematic indices (Defence, Manufacturing, Rural, Capital
Markets, India Digital, Sugar & Ethanol, Railways PSU, ...), same maths.

```
python themes_rrg.py                 # theme table + cross-tailwind stocks
python themes_rrg.py --quad LEADING  # only stocks under leading themes
python themes_rrg.py --refresh       # re-pull prices and NSE membership
python themes_rrg.py --test          # self-check, no network
```

It prints two things. First the thematic quadrant table. Then the **cross-tailwind
screen**: stocks where three things line up at once — the stock's own weekly RRG
quadrant, its sector's, and at least one of its themes'. Sector says the money is
here, theme says the story is here, the stock says this is the name doing the
work. Membership alone is not a tailwind; the group has to be rotating too.

Results are written to `crosswind_<date>.csv`.

### Where theme membership comes from

Unlike the sector baskets, theme membership is **fetched live** from NSE's
published constituent CSVs (`niftyindices.com/IndexConstituent/...`) and cached
for 30 days in `themes_cache.json`. Hand-listing was not an option: the themes
run to 75-stock baskets and reconstitute semi-annually.

Two filename conventions are in play and both are hardcoded in `themes.CSV` —
older indices use `ind_<slug>list.csv`, newer ones `ind_<slug>_list.csv`. NSE
serves its 404 page with HTTP 200, so a fetch is only accepted when the body
actually starts with `Company Name`.

Three published themes have no discoverable CSV slug and are listed in
`themes.UNRESOLVED`: Housing, Infra & Logistics, EV & New Age Automotive.

### Caveats specific to themes

- **Equal-weight, always.** NSE publishes membership but not weights, so a theme
  carried by one mega-cap reads flatter here than its real index does.
- **`n_tail` double-counts correlated themes.** Commodities / Energy / PSE / CPSE
  / Railways PSU share most of their constituents, so PSU energy names score
  high on overlap alone. It is a tiebreak, not independent confirmation.
- The last weekly bar is unclosed — a quadrant can un-cross itself by Friday.

## Reading it as a trade

Rotation is clockwise: Improving -> Leading -> Weakening -> Lagging.

- **Improving** is the early entry — better price, better R:R, and a much higher
  failure rate, because dots routinely curl back down into Lagging without ever
  reaching Leading. Size these smaller and wait for a price trigger.
- **Leading with momentum still rising** is the confirmed trend and the highest
  hit rate. Worse entry price, better odds.
- **Weakening** is an exit zone for longs, not a short signal.
- Read the **tail**, not the dot: a long tail pointing consistently up-and-right
  is a real rotation, a short curling one is churn.
- RRG is **relative**. A leading sector in a falling market is falling *less*.
  Pair it with an absolute filter (price above the 30-week MA) or you will buy
  the best-performing losers.

## Method

Standard JdK RS-Ratio / RS-Momentum:

```
RS        = 100 × price / benchmark
RS-Ratio  = 100 + z-score(RS, window)
RS-Mom    = 100 + z-score(100 × RS-Ratio / RS-Ratio.shift(mom), window)
```

The z-score is over time per name — the standard public reproduction of the
formula. De Kempenaer's original normalises cross-sectionally across the
universe, which pushes names further from the centre. So absolute distances
aren't comparable to StockCharts/Optuma RRGs; quadrants and rotation are.
