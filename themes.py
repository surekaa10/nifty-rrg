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
#   sector_floor  - {industry: minimum aggregate weight}
#   note          - where this simplifies what the document specifies
RULES = {
    "CAPITAL MARKETS":       {"stock_cap": 0.20},
    "COMMODITIES":           {"stock_cap": 0.10},
    "CONGLOMERATE 50":       {"stock_cap": 0.10, "sector_cap": 0.23,
                              "split": "conglomerate",
                              "note": "23% cap is on the business GROUP, via the "
                                      "CONGLOMERATE_GROUP hand map (NSE does not "
                                      "publish the grouping)"},
    "CORE HOUSING":          {"stock_cap": 0.15},
    "CPSE":                  {"stock_cap": 0.20},
    "ENERGY":                {"stock_cap": 0.10, "sector_cap": 0.25},
    "INDIA CONSUMPTION":     {"stock_cap": 0.10},
    "INDIA DEFENCE":         {"stock_cap": 0.20},
    "INDIA DIGITAL":         {"stock_cap": 0.075, "sector_cap": 0.50},
    "INDIA INTERNET":        {"stock_cap": 0.20},
    "INDIA MANUFACTURING":   {"stock_cap": 0.05,
                              "sector_floor": {"Automobile and Auto Components": 0.20,
                                               "Capital Goods": 0.20}},
    "INDIA TOURISM":         {"stock_cap": 0.20},
    "INFRASTRUCTURE":        {"stock_cap": 0.20},
    "MNC":                   {"stock_cap": 0.10},
    "MOBILITY":              {"stock_cap": 0.08, "sector_cap": 0.20,
                              "split": "mobility",
                              "note": "5%/20% on the Table 2 basic industries, 8% "
                                      "elsewhere, via the MOBILITY_BASIC hand map"},
    "NEW AGE CONSUMPTION":   {"stock_cap": 0.05},
    "NON-CYCLICAL CONSUMER": {"stock_cap": 0.10},
    "PSE":                   {"stock_cap": 0.33, "top_n": 3, "top_cap": 0.62},
    "RAILWAYS PSU":          {"stock_cap": 0.20, "split": "railways",
                              "note": "core (Ministry of Railways) 80% / non-core "
                                      "20%, via the RAILWAYS_CORE map"},
    "RURAL":                 {"stock_cap": 0.10, "sector_cap": 0.25},
    "SERVICES":              {"stock_cap": 0.33, "top_n": 3, "top_cap": 0.62},
    "SUGAR & ETHANOL":       {"stock_cap": 0.15},
    "TRANSPORT & LOGISTICS": {"stock_cap": 0.20},
    "WAVES":                 {"stock_cap": 0.05},
}

# --------------------------------------------------------------- hand maps
# NSE's constituent CSV carries the *macro* sector ("Oil Gas & Consumable
# Fuels"), not the *basic industry* ("Refineries & Marketing") that two indices
# cap on. NSE serves basic industry only through an API that 403s/404s
# server-side, so these two maps are hand-listed from the methodology document
# and the constituent lists. Both drift when NSE reconstitutes - re-check them
# against the CSVs after a review.

# Nifty Mobility, Table 2: stocks in these basic industries are capped at 5%
# (rather than 8%) and their corresponding sectors at 20% each.
MOBILITY_TIGHT = {"Abrasives", "Bearings", "Castings & Forgings",
                  "LPG/CNG/PNG/LNG Supplier", "Railway Wagons",
                  "Refineries & Marketing", "Ship Building & Allied Services",
                  "Gas Transmission/Marketing"}

# Nifty Mobility constituents -> basic industry (Table 1 of that section).
MOBILITY_BASIC = {
    "ADANIPORTS": "Port & Port services", "ASHOKLEY": "Commercial Vehicles",
    "BAJAJ-AUTO": "2/3 Wheelers", "BALKRISIND": "Tyres & Rubber Products",
    "BHARATFORG": "Castings & Forgings", "BPCL": "Refineries & Marketing",
    "BOSCHLTD": "Auto Components & Equipments",
    "CONCOR": "Logistics Solution Provider", "EICHERMOT": "2/3 Wheelers",
    "ETERNAL": "E-Commerce delivery", "GAIL": "Gas Transmission/Marketing",
    "GMRAIRPORT": "Airport services", "HEROMOTOCO": "2/3 Wheelers",
    "HINDPETRO": "Refineries & Marketing",
    "HYUNDAI": "Passenger Cars & Utility Vehicles",
    "IOC": "Refineries & Marketing", "IRCTC": "Tour, Travel Related Services",
    "INDIGO": "Airline", "MRF": "Tyres & Rubber Products",
    "M&M": "Passenger Cars & Utility Vehicles",
    "MARUTI": "Passenger Cars & Utility Vehicles",
    "PETRONET": "LPG/CNG/PNG/LNG Supplier", "RELIANCE": "Refineries & Marketing",
    "MOTHERSON": "Auto Components & Equipments",
    "SONACOMS": "Auto Components & Equipments", "SWIGGY": "E-Commerce delivery",
    "TVSMOTOR": "2/3 Wheelers", "TMCV": "Commercial Vehicles",
    "TMPV": "Passenger Cars & Utility Vehicles",
    "TIINDIA": "Auto Components & Equipments",
}

# Nifty Conglomerate 50 constituents -> business group. The document caps the
# GROUP at 23%; NSE does not publish the grouping, so this is read off promoter
# ownership. The Goenka split (RPG Enterprises vs RP-Sanjiv Goenka) and the
# Jindal split are judgement calls - NSE may group them differently.
CONGLOMERATE_GROUP = {
    "ADANIENT": "Adani", "ADANIGREEN": "Adani", "ADANIPORTS": "Adani",
    "ADANIPOWER": "Adani", "AMBUJACEM": "Adani",
    "ABCAPITAL": "Aditya Birla", "GRASIM": "Aditya Birla",
    "HINDALCO": "Aditya Birla", "ULTRACEMCO": "Aditya Birla",
    "IDEA": "Aditya Birla",
    "BAJAJ-AUTO": "Bajaj", "BAJFINANCE": "Bajaj", "BAJAJFINSV": "Bajaj",
    "BAJAJHLDNG": "Bajaj", "BAJAJHFL": "Bajaj",
    "CESC": "RP-Sanjiv Goenka", "PCBL": "RP-Sanjiv Goenka",
    "FSL": "RP-Sanjiv Goenka", "SAREGAMA": "RP-Sanjiv Goenka",
    "CEATLTD": "RPG Enterprises", "KEC": "RPG Enterprises",
    "ZENSARTECH": "RPG Enterprises",
    "CGPOWER": "Murugappa", "CHOLAHLDNG": "Murugappa", "CHOLAFIN": "Murugappa",
    "COROMANDEL": "Murugappa", "TIINDIA": "Murugappa",
    "GODREJCP": "Godrej", "GODREJIND": "Godrej", "GODREJPROP": "Godrej",
    "JSWENERGY": "JSW", "JSWINFRA": "JSW", "JSWSTEEL": "JSW",
    "JSL": "Jindal", "JINDALSTEL": "Jindal",
    "JUBLFOOD": "Jubilant", "JUBLINGREA": "Jubilant", "JUBLPHARMA": "Jubilant",
    "LTF": "L&T", "LTTS": "L&T", "LTM": "L&T", "LT": "L&T",
    "M&MFIN": "Mahindra", "M&M": "Mahindra", "TECHM": "Mahindra",
    "TCS": "Tata", "TMCV": "Tata", "TATASTEEL": "Tata", "TITAN": "Tata",
    "TRENT": "Tata",
}


# Nifty India Railways PSU: "Core Group: Public Sector Undertakings and other
# Organizations functioning under Ministry of Railways". Everything else in the
# index is non-core - PSUs under other ministries that supply or service the
# railways. Administrative ministry is public record, so this is a fact map,
# not a judgement call like CONGLOMERATE_GROUP.
RAILWAYS_CORE = {"IRCTC", "IRFC", "IRCON", "RVNL", "RITES", "RAILTEL", "CONCOR"}


def railways_groups(syms):
    """(core/non-core Series, {group: aggregate cap}) for Nifty Railways PSU."""
    import pandas as pd
    grp = pd.Series({s: ("core" if s in RAILWAYS_CORE else "non-core")
                     for s in syms})
    return grp, {"core": 0.80, "non-core": 0.20}


def mobility_caps(syms):
    """(per-stock cap Series, basic-industry Series, {industry: 20% cap}).

    Mobility is the one index whose stock cap is not uniform: 5% inside the
    Table 2 basic industries, 8% everywhere else, with those sectors held to
    20% each. Symbols missing from the map fall back to the looser 8%.
    """
    import pandas as pd
    basic = pd.Series({s: MOBILITY_BASIC.get(s, "Other") for s in syms})
    cap = pd.Series({s: (0.05 if basic[s] in MOBILITY_TIGHT else 0.08)
                     for s in syms})
    return cap, basic, {b: 0.20 for b in MOBILITY_TIGHT}


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
    # the two hand maps must stay aligned with the rules that consume them
    assert RULES["MOBILITY"]["split"] == "mobility"
    # Table 2 is a standing list; only some of it appears in any given
    # membership, so overlap is what matters, not coverage either way.
    assert MOBILITY_TIGHT & set(MOBILITY_BASIC.values()), "no tight industry mapped"
    cap, basic, gc = mobility_caps(["RELIANCE", "MARUTI", "NOTALISTED"])
    assert cap["RELIANCE"] == 0.05 and cap["MARUTI"] == 0.08, dict(cap)
    assert cap["NOTALISTED"] == 0.08, "unmapped symbol must take the loose cap"
    assert basic["RELIANCE"] == "Refineries & Marketing"
    assert gc["Refineries & Marketing"] == 0.20
    assert len(set(CONGLOMERATE_GROUP.values())) >= 10, "groups look collapsed"
    g, gc = railways_groups(["IRCTC", "ONGC", "RVNL"])
    assert list(g) == ["core", "non-core", "core"], list(g)
    assert gc == {"core": 0.80, "non-core": 0.20}
    assert RULES["INDIA MANUFACTURING"]["sector_floor"]["Capital Goods"] == 0.20
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
