#!/usr/bin/env python3
"""
Multifamily & Development Briefings — data fetcher.

Pulls real-estate RSS feeds + a few market rates, asks Claude Haiku 4.5 to write
a 3-bullet "key points" list and a short extended summary for each story, and
writes everything to data.json (which the dashboard reads).

Run locally:   ANTHROPIC_API_KEY=sk-ant-... python3 fetch_news.py
In GitHub Actions the key comes from the repo secret ANTHROPIC_API_KEY.
"""

import json, os, re, ssl, time, urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import anthropic

# ---------------------------------------------------------------------------
# CONFIG  —  edit sources here. Each: (feed_url, source_name, category, max_items)
# Most real-estate sites' feeds are their URL + /feed/.
# ---------------------------------------------------------------------------
FEEDS = [
    ("https://www.multihousingnews.com/feed/",                 "Multi-Housing",  "Multifamily", 6),
    ("https://therealdeal.com/category/development/feed/",     "TRD Development", "Development", 6),
    ("https://commercialobserver.com/feed/",                  "Comm. Observer", "Commercial",  5),
    ("https://www.connectcre.com/feed/",                      "Connect CRE",    "Commercial",  5),
    ("https://www.commercialsearch.com/news/feed/",           "CPE",            "Commercial",  4),
    ("https://therealdeal.com/feed/",                         "TRD National",   "National",    4),
    ("https://propmodo.com/real-estate/feed/",                "Propmodo",       "PropTech",    3),
]

MAX_STORIES   = 30                       # hard cap to control API cost
RECENCY_HOURS = 96                       # keep stories from the last 4 days
MODEL         = "claude-haiku-4-5"       # $1 / $5 per M tokens
OUTPUT_FILE   = "data.json"

# Market rates relevant to development underwriting
YAHOO_TICKERS = [("^TNX", "10Y Treasury", "%"),
                 ("^TYX", "30Y Treasury", "%"),
                 ("VNQ",  "REIT Index (VNQ)", "")]

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; TCR-Briefings/1.0)"}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"
_ATOM_NS = "http://www.w3.org/2005/Atom"


# --------------------------- small helpers --------------------------------
def fetch(url, timeout=12):
    req = urllib.request.Request(url, headers=HEADERS)
    return urllib.request.urlopen(req, timeout=timeout, context=_CTX).read()


def strip_html(text, limit=2000):
    if not text:
        return ""
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def parse_date(text):
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def is_recent(dt):
    if dt is None:
        return True
    age = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    return age <= RECENCY_HOURS


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()[:70]


# --------------------------- feed parsing ---------------------------------
def parse_feed(raw, source, cat, limit):
    out = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return out

    items = root.findall(".//item")[:limit]
    for it in items:
        title = (it.findtext("title") or "").strip()
        if not title:
            continue
        link = (it.findtext("link") or "").strip()
        content = it.find(f"{{{_CONTENT_NS}}}encoded")
        desc = strip_html(content.text) if (content is not None and content.text) else strip_html(it.findtext("description"))
        dt = parse_date(it.findtext("pubDate"))
        out.append({"title": title, "link": link, "excerpt": desc,
                    "source": source, "cat": cat,
                    "date": dt.isoformat() if dt else None, "_dt": dt})

    if out:
        return out

    # Atom fallback
    ns = {"a": _ATOM_NS}
    for entry in root.findall(".//a:entry", ns)[:limit]:
        title = (entry.findtext("a:title", namespaces=ns) or "").strip()
        if not title:
            continue
        link_el = entry.find("a:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        summary = strip_html(entry.findtext("a:summary", namespaces=ns) or entry.findtext("a:content", namespaces=ns))
        dt = parse_date(entry.findtext("a:updated", namespaces=ns) or entry.findtext("a:published", namespaces=ns))
        out.append({"title": title, "link": link, "excerpt": summary,
                    "source": source, "cat": cat,
                    "date": dt.isoformat() if dt else None, "_dt": dt})
    return out


def gather_stories():
    def one(args):
        url, source, cat, limit = args
        try:
            return parse_feed(fetch(url), source, cat, limit)
        except Exception as e:
            print(f"  ! feed failed: {source} ({e})")
            return []

    all_items = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for batch in ex.map(one, FEEDS):
            all_items.extend(batch)

    # de-dupe by title, keep recent, sort newest first, cap
    seen, deduped = set(), []
    for it in all_items:
        k = norm_title(it["title"])
        if k in seen or not is_recent(it["_dt"]):
            continue
        seen.add(k)
        deduped.append(it)

    deduped.sort(key=lambda x: x["_dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return deduped[:MAX_STORIES]


def fetch_article_text(url, timeout=8):
    """Best-effort body text to give the model more to work with."""
    if not url:
        return ""
    try:
        raw = fetch(url, timeout=timeout).decode("utf-8", errors="ignore")
        paras = re.findall(r"<p[^>]*>(.*?)</p>", raw, re.DOTALL | re.IGNORECASE)
        parts = []
        for p in paras:
            t = strip_html(p, limit=600)
            if len(t) > 60:
                parts.append(t)
            if sum(len(x) for x in parts) >= 1800:
                break
        return " ".join(parts)[:1800]
    except Exception:
        return ""


# --------------------------- market rates ---------------------------------
def fetch_rates():
    cells = []

    def one(t):
        sym, label, suffix = t
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=5d"
            d = json.loads(fetch(url))
            res = d["chart"]["result"][0]
            price = float(res["meta"].get("regularMarketPrice") or 0)
            closes = [c for c in (res.get("indicators", {}).get("quote", [{}])[0].get("close", []) or []) if c is not None]
            pct = res["meta"].get("regularMarketChangePercent")
            if len(closes) >= 2:
                pct = (closes[-1] - closes[-2]) / closes[-2] * 100
            value = f"{round(price):,}{suffix}" if price >= 1000 else f"{price:.2f}{suffix}"
            return {"label": label, "value": value, "pct": round(pct, 2) if pct is not None else None}
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=4) as ex:
        for c in ex.map(one, YAHOO_TICKERS):
            if c:
                cells.append(c)

    # SOFR from the New York Fed
    try:
        from datetime import date, timedelta
        end, start = date.today(), date.today() - timedelta(days=14)
        url = (f"https://markets.newyorkfed.org/read?productCode=50"
               f"&startDt={start.isoformat()}&endDt={end.isoformat()}&eventCodes=520&format=json")
        rates = json.loads(fetch(url)).get("refRates", [])
        if rates:
            pct = None
            if len(rates) >= 2:
                pct = round(float(rates[0]["percentRate"]) - float(rates[1]["percentRate"]), 4)
            cells.insert(0, {"label": "SOFR",
                             "value": f'{float(rates[0]["percentRate"]):.2f}%',
                             "pct": pct, "isBps": True})
    except Exception:
        pass

    return cells


# --------------------------- summarization --------------------------------
def summarize(stories):
    if not stories:
        return
    bodies = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fetch_article_text, s.get("link", "")): i for i, s in enumerate(stories)}
        for f in futs:
            bodies[futs[f]] = f.result()

    numbered = "\n\n".join(
        f"[{i+1}] {s['title']}\n{(bodies.get(i) or s.get('excerpt') or '(no body)')[:1800]}"
        for i, s in enumerate(stories)
    )

    prompt = (
        "You are the morning analyst for a multifamily real estate DEVELOPMENT team at "
        "Trammell Crow Residential. Your readers acquire land, underwrite, finance, and build "
        "apartment communities across major U.S. markets. They care less about the headline itself "
        "and more about what each story means for apartment DEMAND, SUPPLY, CAPITAL, and COST in "
        "specific markets.\n\n"
        "For each numbered article, return:\n"
        "- \"bullets\": exactly 3 key points, each under 12 words, leading with concrete facts "
        "(parties, numbers, locations).\n"
        "- \"summary\": 3-5 tight sentences with the facts and the most important figures.\n"
        "- \"impact\": 1-2 sentences on WHY IT MATTERS TO A MULTIFAMILY DEVELOPER — the second-order "
        "market effect, not a restatement of the news. Name the affected metro/submarket when relevant.\n\n"
        "Pay special attention to DEMAND AND MARKET SIGNALS that aren't always obvious, and spell out "
        "the chain of effects. Examples of what to flag and how to reason:\n"
        "- Corporate relocations, HQ moves, major employer expansions or layoffs. "
        "(e.g., a large employer moving to a metro -> job and population in-migration -> stronger "
        "rental demand and rent growth in that metro's submarkets, and a window to acquire/build ahead "
        "of it.)\n"
        "- Population migration, job growth, and large new facilities (plants, campuses, data centers).\n"
        "- Interest rates, SOFR, cap rates, and financing availability -> underwriting, valuations, starts.\n"
        "- Construction costs, materials, labor, tariffs, supply-chain shifts -> development feasibility.\n"
        "- Housing supply pipeline, deliveries, and absorption in specific markets.\n"
        "- Zoning, entitlement, tax (TIF/PILOT/abatement), and regulatory changes.\n\n"
        "Use your own knowledge of companies, markets, and the industry to connect the dots even when "
        "the article doesn't state the implication. If a story genuinely has little relevance to "
        "multifamily development, say so briefly and honestly in \"impact\".\n\n"
        "Be specific and direct; no scene-setting. Return ONLY a JSON array, no prose, no markdown fences:\n"
        "[{\"idx\":1,\"bullets\":[\"...\",\"...\",\"...\"],\"summary\":\"...\",\"impact\":\"...\"}, ...]\n\n"
        + numbered
    )

    client = anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if not match:
            print("  ! model returned no JSON array")
            return
        by_idx = {s["idx"]: s for s in json.loads(match.group())}
        for i, story in enumerate(stories):
            s = by_idx.get(i + 1)
            if s:
                story["bullets"] = s.get("bullets", [])
                story["summary"] = s.get("summary", "")
                story["impact"]  = s.get("impact", "")
    except Exception as e:
        print(f"  ! summarization failed: {e}")


# --------------------------- main -----------------------------------------
def main():
    print("Gathering stories…")
    stories = gather_stories()
    print(f"  {len(stories)} stories after de-dupe")

    print("Fetching rates…")
    rates = fetch_rates()

    print("Summarizing with Claude…")
    summarize(stories)

    for s in stories:
        s.pop("_dt", None)

    out = {
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "rates": rates,
        "stories": stories,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"Wrote {OUTPUT_FILE} ({len(stories)} stories, {len(rates)} rate cells).")


if __name__ == "__main__":
    main()
