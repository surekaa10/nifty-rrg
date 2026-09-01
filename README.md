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

**6 are rebuilt from their constituents** — `CAPITAL MRKT`, `CHEMICALS`,
`CONSR DURBL`, `DEFENCE`, `HEALTHCARE`, `OIL & GAS`. Yahoo resolves these index
symbols but serves a live quote with *zero* history, and NSE's own API returns
403 to server-side requests. So the app rebuilds them from their constituents
(listed in `CONSTITUENTS` in `sectors.py`) the way the methodology document
specifies: free-float market-cap weighted, with the sectoral 33% stock cap and
62% cumulative cap on the top 3. The table marks each row `NSE index` or
`ff-cap 33%`.

Caveat on the rebuilt sectors: membership is hand-listed and NSE rebalances, and
the float figures are today's rather than the 6-month averages NSE reviews on,
so index *levels* won't match NSE's published values. Rotation direction and
quadrant are what the chart is for, and those hold. Edit `CONSTITUENTS` in
`sectors.py` to correct membership. See `weights.py` for the full list of what
this construction can and cannot reproduce.

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

It prints two things. First the thematic quadrant table (each row labelled with the cap that built it). Then the **cross-tailwind
screen**: stocks where three things line up at once — the stock's own weekly RRG
quadrant, its sector's, and at least one of its themes'. Sector says the money is
here, theme says the story is here, the stock says this is the name doing the
work. Membership alone is not a tailwind; the group has to be rotating too.

Results are written to `crosswind_<date>.csv`.

### Where theme membership comes from

Theme membership is **fetched live** from NSE's published constituent CSVs
(`niftyindices.com/IndexConstituent/...`) and cached for 30 days in
`themes_cache.json`. Hand-listing was not an option: the themes run to 75-stock
baskets and reconstitute semi-annually.

Two filename conventions are in play and both are hardcoded in `themes.CSV` --
older indices use `ind_<slug>list.csv`, newer ones `ind_<slug>_list.csv`. NSE
serves its 404 page with HTTP 200, so a fetch is only accepted when the body
actually starts with `Company Name`.

Three published themes have no discoverable CSV slug and are listed in
`themes.UNRESOLVED`: Housing, Infra & Logistics, EV & New Age Automotive.

### How the indices are constructed

Per `Method_NIFTY_Equity_Indices.pdf` (August 2026), **every Nifty thematic
index is free-float market-cap weighted and then capped** -- none is equal
weight. The caps differ per index and are transcribed into `themes.RULES`:

```
python themes.py --rules
```

`weights.py` applies them the way NSE does: weight by free-float market cap,
then repeatedly take the excess off whatever breaches a cap and redistribute it
pro-rata across the names that don't, until nothing breaches. Sector caps,
and the top-3-cumulative cap used by PSE and Services, work the same way.
Weights are set at a semi-annual rebalance and then drift with price until the
next one, as the document specifies.

The six sector indices Yahoo has no history for are rebuilt the same way, under
the sectoral standard of a 33% stock cap and 62% cumulative cap on the top 3.

Weighting is not cosmetic: on identical data, **6 of the 24 themes sit in a
different quadrant** under the document's weighting than under equal weight.

### What this cannot reproduce

- NSE weights off the **6-month average** free-float mcap at each review date.
  Yahoo publishes one current `floatShares`, so today's float is applied across
  the whole history.
- NSE publishes **only current constituents**. Backfilling today's membership
  over three years of prices is survivorship bias, and no free source fixes it.
- NSE's **IWF** is an exact published figure; `floatShares / sharesOutstanding`
  is Yahoo's estimate of the same thing.
- Two caps depend on data absent from the constituent CSV, which carries the
  *macro* sector ("Oil Gas & Consumable Fuels") rather than the *basic industry*
  ("Refineries & Marketing") the document caps on. NSE serves basic industry
  only through an API that refuses server-side requests, so `themes.py`
  hand-maps both: `MOBILITY_BASIC` (Table 2 of that section -- 5% on those
  stocks and 20% on their sectors, 8% elsewhere) and `CONGLOMERATE_GROUP`
  (business group, for the 23% group cap). Re-check both after an NSE review.
  Railways PSU splits on administrative ministry -- `RAILWAYS_CORE` lists the
  PSUs under the Ministry of Railways, which the document defines as the core
  group (80%) against non-core (20%). Without that cap the index is effectively
  an oil-and-power index: the actual railway companies carry only **12%** of
  free-float weight, because ONGC, NTPC, BPCL and SAIL dwarf them.
- India Manufacturing's 20% sector floors (Automobile and Auto Components,
  Capital Goods) read straight off the CSV's Industry column, and are relaxed
  for a sector holding fewer than four names, as the document specifies.

So index **levels** will not match NSE's published values. Quadrant and rotation
direction -- what an RRG is actually read for -- are far less sensitive to all
four than a level is.

### Other caveats

- **`n_tail` double-counts correlated themes.** Commodities / Energy / PSE /
  CPSE / Railways PSU share most of their constituents, so PSU energy names
  score high on overlap alone. It is a tiebreak, not independent confirmation.
- The last weekly bar is unclosed -- a quadrant can un-cross itself by Friday.

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
