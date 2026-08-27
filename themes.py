"""Nifty thematic index constituents, pulled live from NSE.

Unlike `sectors.py` (hand-listed, because these lists are small and stable),
the thematic indices are wide, numerous, and rebalance semi-annually - so the
membership is fetched from NSE's published constituent CSVs and cached.

    python themes.py            # show what resolves, and how stale the cache is
    python themes.py --refresh  # re-pull every list from NSE
    python themes.py --test     # self-check, no network

ponytail: no free-float weights - NSE publishes membership, not weights, so
every theme is an equal-weight basket. A theme with one dominant stock will
therefore look different here than the real index does. Upgrade path: scrape
weights from the factsheet PDFs if a basket ever visibly diverges.
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


def parse(text: str) -> list[str]:
    """Symbols out of an NSE constituent CSV. Empty if this isn't one.

    Read with the csv module, not split(","): company names are quoted and
    contain commas ("Entertainment & Publication, Ltd.") - splitting shifts
    every column after the first.
    """
    text = text.lstrip()
    if not text.startswith("Company Name"):
        return []                      # NSE serves its 404 page with status 200
    rows = csv.DictReader(io.StringIO(text))
    if "Symbol" not in (rows.fieldnames or []):
        return []
    return [r["Symbol"].strip() for r in rows if (r.get("Symbol") or "").strip()]


def fetch(refresh: bool = False) -> dict[str, list[str]]:
    """{theme: [symbols]}, from cache unless it is missing, stale, or refused."""
    if CACHE.exists() and not refresh:
        blob = json.loads(CACHE.read_text())
        age = (date.today() - date.fromisoformat(blob["fetched"])).days
        if age < STALE_DAYS:
            return blob["themes"]

    import requests
    out = {}
    for theme, name in CSV.items():
        try:
            r = requests.get(BASE + name, timeout=30,
                             headers={"User-Agent": "Mozilla/5.0"})
            syms = parse(r.text)
        except Exception:
            syms = []
        if syms:
            out[theme] = syms
    if not out:                        # network down - stale beats nothing
        if CACHE.exists():
            return json.loads(CACHE.read_text())["themes"]
        raise RuntimeError("no themes fetched and no cache to fall back on")
    CACHE.write_text(json.dumps(
        {"fetched": date.today().isoformat(), "themes": out}, indent=1))
    return out


def demo():
    good = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
            "Foo Ltd.,Power,FOO,EQ,INE1\n")
    assert parse(good) == ["FOO"]
    assert parse("<!DOCTYPE html><html>404") == [], "NSE 404 page must parse empty"
    assert parse("") == []
    # a quoted comma inside the company name must not shift the Symbol column
    quoted = ("Company Name,Industry,Symbol,Series,ISIN Code\n"
              '"Zee Entertainment, Ltd.",Media,ZEEL,EQ,INE2\n')
    assert parse(quoted) == ["ZEEL"], parse(quoted)
    assert not (set(CSV) & set(UNRESOLVED)), "theme both resolved and unresolved"
    print("ok")


if __name__ == "__main__":
    import sys
    if "--test" in sys.argv:
        demo(); raise SystemExit
    t = fetch("--refresh" in sys.argv)
    for k, v in sorted(t.items()):
        print(f"{k:24s} {len(v):3d}  {' '.join(v[:6])} ...")
    print(f"\n{len(t)} themes, cache {CACHE.name}, "
          f"fetched {json.loads(CACHE.read_text())['fetched']}")
    print("unresolved:", ", ".join(UNRESOLVED))
