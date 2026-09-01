"""Nifty thematic index constituents and construction rules, from NSE.

Membership is fetched from NSE's published constituent CSVs and cached. The
per-index construction rules in `RULES` are transcribed from
`Method_NIFTY_Equity_Indices.pdf` (August 2026) - every Nifty thematic index is
free-float market-cap weighted, then capped, and the caps differ per index.

    python themes.py            # show what resolves, and how stale the cache is
    python themes.py --rules    # the capping rules, and where they approximate
    python themes.py --refresh  # re-pull every list from NSE
    python themes.py --test     # self-check, no network

Unlike `sectors.py` (hand-listed, small and stable), thematic membership runs
to 75-stock baskets that reconstitute semi-annually, so it is pulled live.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import date
from pathlib import Path

HERE = Path(__file__).parent
CACHE = HERE / "themes_cache.json"
BASE = "https://niftyindices.com/IndexConstituent/"
STALE_DAYS = 30                       # NSE reconstitutes semi-annually

# theme -> NSE constituent-CSV filename. Older indices use `...list.csv`,
# newer ones `..._list.csv`; both patterns live here deliberately.
CSV = {
    "CAPITAL MARKETS": "ind_niftycapitalmarkets_list.csv",
    "COMMODITIES": "ind_niftycommoditieslist.csv",
    "CONGLOMERATE 50": "ind_niftyconglomerate50_list.csv",
    "CORE HOUSING": "ind_niftycorehousing_list.csv",
    "CPSE": "ind_niftycpselist.csv",
    "ENERGY": "ind_niftyenergylist.csv",
    "INDIA CONSUMPTION": "ind_niftyconsumptionlist.csv",
    "INDIA DEFENCE": "ind_niftyindiadefence_list.csv",
    "INDIA DIGITAL": "ind_niftyindiadigital_list.csv",
    "INDIA INTERNET": "ind_niftyindiainternet_list.csv",
    "INDIA MANUFACTURING": "ind_niftyindiamanufacturing_list.csv",
    "INDIA TOURISM": "ind_niftyindiatourism_list.csv",
    "INFRASTRUCTURE": "ind_niftyinfralist.csv",
    "MNC": "ind_niftymnclist.csv",
    "MOBILITY": "ind_niftymobility_list.csv",
    "NEW AGE CONSUMPTION": "ind_niftyindianewageconsumption_list.csv",
    "NON-CYCLICAL CONSUMER": "ind_niftynon-cyclicalconsumer_list.csv",
    "PSE": "ind_niftypselist.csv",
    "RAILWAYS PSU": "ind_niftyindiarailwayspsu_list.csv",
    "RURAL": "ind_niftyrural_list.csv",
    "SERVICES": "ind_niftyservicelist.csv",
    "SUGAR & ETHANOL": "ind_niftysugarethanol_list.csv",
    "TRANSPORT & LOGISTICS": "ind_niftytransportationandlogistics_list.csv",
    "WAVES": "ind_niftywaves_list.csv",
}
# Published by NSE but no CSV slug found - add by hand if one turns up.
UNRESOLVED = ["HOUSING", "INFRA & LOGISTICS", "EV & NEW AGE AUTOMOTIVE"]

# Construction rules, per the methodology document. Every index is weighted by
# free-float market capitalisation; these are the caps applied on top.
#   stock_cap     - maximum weight of any one constituent
#   sector_cap    - maximum weight of any one industry (grouped on the Industry
#                   column NSE ships in the constituent CSV)
#   top_n/top_cap - cumulative cap on the N largest constituents
#   note          - where this simplifies what the document specifies
RULES = {
    "CAPITAL MARKETS":       {"stock_cap": 0.20},
    "COMMODITIES":           {"stock_cap": 0.10},
    "CONGLOMERATE 50":       {"stock_cap": 0.10, "sector_cap": 0.23,
                              "note": "doc caps the conglomerate GROUP at 23%; "
                                      "group membership is not in the CSV, so "
                                      "the industry column stands in"},
    "CORE HOUSING":          {"stock_cap": 0.15},
    "CPSE":                  {"stock_cap": 0.20},
    "ENERGY":                {"stock_cap": 0.10, "sector_cap": 0.25},
    "INDIA CONSUMPTION":     {"stock_cap": 0.10},
    "INDIA DEFENCE":         {"stock_cap": 0.20},
    "INDIA DIGITAL":         {"stock_cap": 0.075, "sector_cap": 0.50},
    "INDIA INTERNET":        {"stock_cap": 0.20},
    "INDIA MANUFACTURING":   {"stock_cap": 0.05,
                              "note": "doc also sets a 20% MINIMUM to certain "
                                      "manufacturing sectors; floors not applied"},
    "INDIA TOURISM":         {"stock_cap": 0.20},
    "INFRASTRUCTURE":        {"stock_cap": 0.20},
    "MNC":                   {"stock_cap": 0.10},
    "MOBILITY":              {"stock_cap": 0.08, "sector_cap": 0.20,
                              "note": "doc caps stocks in listed basic industries "
                                      "at 5% and all others at 8%; the 8% applies "
                                      "to all here (basic-industry list not in CSV)"},
    "NEW AGE CONSUMPTION":   {"stock_cap": 0.05},
    "NON-CYCLICAL CONSUMER": {"stock_cap": 0.10},
    "PSE":                   {"stock_cap": 0.33, "top_n": 3, "top_cap": 0.62},
    "RAILWAYS PSU":          {"stock_cap": 0.20,
                              "note": "doc caps the core group at 80% and non-core "
                                      "at 20%; that split is not in the CSV"},
    "RURAL":                 {"stock_cap": 0.10, "sector_cap": 0.25},
    "SERVICES":              {"stock_cap": 0.33, "top_n": 3, "top_cap": 0.62},
    "SUGAR & ETHANOL":       {"stock_cap": 0.15},
    "TRANSPORT & LOGISTICS": {"stock_cap": 0.20},
    "WAVES":                 {"stock_cap": 0.05},
}


def parse(text: str) -> list[dict]:
    """[{symbol, industry}] out of an NSE constituent CSV. Empty if not one.

    Read with the csv module, not split(","): company names are quoted and
    contain commas ("Zee Entertainment, Ltd."), which shifts every column
    after the first.
    """
    text = text.lstrip()
    if not text.startswith("Company Name"):
        return []                      # NSE serves its 404 page with status 200
    rows = csv.DictReader(io.StringIO(text))
    if "Symbol" not in (rows.fieldnames or []):
        return []
    return [{"symbol": r["Symbol"].strip(),
             "industry": (r.get("Industry") or "").strip() or "Unknown"}
            for r in rows if (r.get("Symbol") or "").strip()]


def fetch(refresh: bool = False) -> dict[str, list[dict]]:
    """{theme: [{symbol, industry}]}, cached unless missing, stale or refused."""
    if CACHE.exists() and not refresh:
        blob = json.loads(CACHE.read_text())
        age = (date.today() - date.fromisoformat(blob["fetched"])).days
        if age < STALE_DAYS and blob.get("schema") == 2:
            return blob["themes"]

    import requests
    out = {}
    for theme, name in CSV.items():
        try:
            r = requests.get(BASE + name, timeout=30,
                             headers={"User-Agent": "Mozilla/5.0"})
            rows = parse(r.text)
        except Exception:
            rows = []
        if rows:
            out[theme] = rows
    if not out:                        # network down - stale beats nothing
        if CACHE.exists():
            return json.loads(CACHE.read_text())["themes"]
        raise RuntimeError("no themes fetched and no cache to fall back on")
    CACHE.write_text(json.dumps({"fetched": date.today().isoformat(),
                                 "schema": 2, "themes": out}, indent=1))
    return out


def symbols(rows) -> list[str]:
    """Just the tickers out of a theme's constituent rows."""
    return [r["symbol"] for r in rows]


def demo():
    good = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
            "Foo Ltd.,Power,FOO,EQ,INE1\n")
    assert parse(good) == [{"symbol": "FOO", "industry": "Power"}]
    assert symbols(parse(good)) == ["FOO"]
    assert parse("<!DOCTYPE html><html>404") == [], "NSE 404 page must parse empty"
    assert parse("") == []
    # a quoted comma inside the company name must not shift the Symbol column
    quoted = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
              '"Zee Entertainment, Ltd.",Media,ZEEL,EQ,INE2\n')
    assert symbols(parse(quoted)) == ["ZEEL"], parse(quoted)
    assert parse(quoted)[0]["industry"] == "Media"
    assert not (set(CSV) & set(UNRESOLVED)), "theme both resolved and unresolved"
    # every fetchable theme must carry a construction rule, and vice versa
    assert set(RULES) == set(CSV), set(RULES) ^ set(CSV)
    assert all(0 < r["stock_cap"] <= 1 for r in RULES.values()), "bad stock cap"
    print("ok")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        demo(); raise SystemExit
    if "--rules" in sys.argv:
        print(f"{'THEME':24s} {'STOCK':>6} {'SECTOR':>7} {'TOP-N':>8}  note")
        for k, v in sorted(RULES.items()):
            top = (f"{v['top_n']}<={v['top_cap']:.0%}" if v.get("top_n") else "-")
            sec = (f"{v['sector_cap']:.0%}" if v.get("sector_cap") else "-")
            print(f"{k:24s} {v['stock_cap']:>6.1%} {sec:>7} {top:>8}  "
                  f"{v.get('note', '')}")
        raise SystemExit
    t = fetch("--refresh" in sys.argv)
    for k, v in sorted(t.items()):
        print(f"{k:24s} {len(v):3d}  {' '.join(symbols(v)[:6])} ...")
    print(f"\n{len(t)} themes, cache {CACHE.name}, "
          f"fetched {json.loads(CACHE.read_text())['fetched']}")
    print("unresolved:", ", ".join(UNRESOLVED))
