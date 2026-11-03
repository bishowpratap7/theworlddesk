#!/usr/bin/env python3
"""The World Desk scanner: aggregates world-news headlines from the public
RSS feeds of established outlets (BBC, Guardian, Al Jazeera, NPR, DW,
France 24) plus Google News topic searches when reachable. No API keys.
Sections: Top World, Wars & Conflicts, Politics & Policy, Global Economy.
Each desk fails independently; on failure it keeps the previous data,
flagged stale. Stdlib only."""
import json, os, re, subprocess, sys, urllib.request, xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "wire.json")
SITE = "https://bishowpratap7.github.io/theworlddesk/"
UA = {"User-Agent": "WorldDesk/1.0 (by Bishow Pandey; +https://x.com/bishowppandey)"}

OUTLETS = [
    ("BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("The Guardian", "https://www.theguardian.com/world/rss"),
    ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("NPR", "https://feeds.npr.org/1004/rss.xml"),
    ("DW", "https://rss.dw.com/rdf/rss-en-world"),
    ("France 24", "https://www.france24.com/en/rss"),
]
GN = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
WAR_Q = "Ukraine+war+OR+Gaza+OR+ceasefire+OR+conflict"
POL_Q = "election+OR+sanctions+OR+diplomacy+OR+policy"

WAR = ["ukraine", "kyiv", "russia", "moscow", "putin", "zelensky", "gaza", "israel", "hamas",
       "hezbollah", "lebanon", "iran", "syria", "war", "conflict", "ceasefire", "missile",
       "drone", "airstrike", "air strike", "troops", "military", "nato", "frontline",
       "offensive", "invasion", "hostage", "houthi", "sudan", "myanmar", "taiwan strait"]
POL = ["election", "president", "prime minister", "parliament", "congress", "senate", "policy",
       "government", "minister", "diplomat", "diplomacy", "summit", "treaty", "sanctions",
       "immigration", "supreme court", "lawmakers", "european union", "united nations",
       "security council", "white house", "cabinet", "referendum", "protest", "tariff deal"]
ECON = ["tariff", "trade war", "inflation", "central bank", "interest rate", "oil price",
        "markets", "stocks", "recession", "imf", "world bank", "opec", "supply chain",
        "exports", "imports", "currency", "gdp", "economic growth"]
BLOCK = ["premier league", "nfl", "nba", "oscars", "box office", "world cup qualifier"]

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers={**UA, "Accept": "application/rss+xml, application/xml, text/xml"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except Exception:
        out = subprocess.run(["curl", "-sL", "--max-time", str(timeout), "-A", UA["User-Agent"], url],
                             capture_output=True, text=True)
        if out.returncode != 0 or not out.stdout:
            raise RuntimeError(f"fetch failed: {url}")
        return out.stdout

def strip_html(s):
    s = re.sub(r"<[^>]+>", "", s or "")
    return re.sub(r"\s+", " ", s).strip()

def parse_rss(raw, default_source):
    items = []
    root = ET.fromstring(raw)
    chan = root.find("channel") or root
    for it in chan.findall("item"):
        def txt(tag):
            e = it.find(tag)
            return (e.text or "").strip() if e is not None and e.text else ""
        title = txt("title")
        link = txt("link")
        if not title or not link:
            continue
        src = default_source
        se = it.find("source")
        if se is not None and se.text:
            src = se.text.strip()
        title = re.sub(r"\s+-\s+" + re.escape(src) + r"$", "", title)
        pub = None
        ptxt = txt("pubDate") or txt("{http://purl.org/dc/elements/1.1/}date")
        if ptxt:
            try:
                pub = parsedate_to_datetime(ptxt).astimezone(timezone.utc).isoformat()
            except Exception:
                pub = None
        img = ""
        for tag in ("{http://search.yahoo.com/mrss/}thumbnail", "{http://search.yahoo.com/mrss/}content"):
            e = it.find(tag)
            if e is not None and e.get("url"):
                img = e.get("url"); break
        items.append({"title": title, "url": link, "source": src, "published": pub,
                      "summary": strip_html(txt("description"))[:180], "image": img})
    return items

def norm_key(t):
    t = re.sub(r"[^a-z0-9 ]", "", t.lower())
    return " ".join(t.split()[:8])

def dedupe(items):
    seen, out = set(), []
    for it in items:
        k = norm_key(it["title"])
        if k in seen:
            continue
        seen.add(k); out.append(it)
    return out

def fetch_pool():
    pool = []
    for name, url in OUTLETS:
        try:
            got = parse_rss(fetch(url), name)
            print(f"  outlet {name}: {len(got)}", file=sys.stderr)
            pool.extend(got)
        except Exception as e:
            print(f"  outlet {name}: {e}", file=sys.stderr)
    return pool

def gn_pool(query):
    try:
        return parse_rss(fetch(GN.format(q=query)), "Google News")
    except Exception as e:
        print(f"  google news: {e}", file=sys.stderr)
        return []

def matches(it, words):
    hay = (it["title"] + " " + it.get("summary", "")).lower()
    return any(w in hay for w in words)

def clean(items):
    items = [i for i in items if not matches(i, BLOCK)]
    items = dedupe(items)
    items.sort(key=lambda i: i.get("published") or "", reverse=True)
    return items

def build_sections():
    pool = clean(fetch_pool())
    if not pool:
        raise RuntimeError("all outlets unreachable")
    war_pool = clean(gn_pool(WAR_Q) + [i for i in pool if matches(i, WAR)])
    pol_pool = clean(gn_pool(POL_Q) + [i for i in pool if matches(i, POL)])
    war_keys = {norm_key(i["title"]) for i in war_pool}
    pol_only = [i for i in pol_pool if norm_key(i["title"]) not in war_keys]
    econ = clean([i for i in pool if matches(i, ECON)])
    return {
        "top_world": pool[:14],
        "wars": war_pool[:12],
        "politics": (pol_only or pol_pool)[:12],
        "economy": econ[:10],
    }, None

SECTION_DEFS = [
    ("top_world", "🌍 Top World Headlines"),
    ("wars", "⚔️ Wars & Conflicts"),
    ("politics", "🏛️ Politics & Policy"),
    ("economy", "📈 Global Economy & Trade"),
]

def main():
    prev = {}
    if os.path.exists(OUT):
        try:
            prev = json.load(open(OUT)).get("sections", {})
        except Exception:
            prev = {}
    try:
        fresh, _ = build_sections()
    except Exception as e:
        fresh = {}
        print(f"pool failed: {e}", file=sys.stderr)
    sections, report = {}, []
    for name, _title in SECTION_DEFS:
        items = fresh.get(name)
        if items:
            sections[name] = {"fetched_at": now_iso(), "stale": False, "items": items}
            report.append(f"{name}: OK ({len(items)})")
        else:
            old = prev.get(name)
            if old:
                old = dict(old); old["stale"] = True
                sections[name] = old
                report.append(f"{name}: no fresh data — kept previous, marked stale")
            else:
                sections[name] = {"fetched_at": None, "stale": True, "items": []}
                report.append(f"{name}: no data")
    prev_items = {k: (v or {}).get("items", []) for k, v in prev.items()}
    new_items = {k: v["items"] for k, v in sections.items()}
    if prev and prev_items == new_items:
        print("World Desk scan: no content change — files untouched")
        return
    if not any(new_items.values()):
        print("World Desk scan: no data at all — leaving files untouched")
        return
    wire = {"generated_at": now_iso(), "sections": sections}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump(wire, open(OUT, "w"), indent=1)
    print("World Desk scan @", now_iso())
    for line in report:
        print(" ", line)
    publish_static(wire)

def esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

def publish_static(wire):
    import html as H
    s = wire["sections"]; gen = wire["generated_at"]
    def card(it):
        img = (f'<img loading="lazy" src="{esc(it.get("image"))}" alt="">' if it.get("image") else "")
        when = (it.get("published") or "")[:16].replace("T", " ")
        return (f'<div class="baked-card"><span class="src">{esc(it.get("source"))} · {esc(when)} UTC</span>'
                f'<a href="{esc(it.get("url"))}">{esc(it.get("title"))}</a>{img}'
                f'<span class="meta">{esc(it.get("summary"))}</span></div>')
    blocks = [f'<p class="baked-updated">Wire updated {esc(gen)} UTC — aggregated by '
              f'<a href="https://x.com/bishowppandey">Bishow Pandey</a> from BBC News, The Guardian, '
              f'Al Jazeera, NPR, DW and France 24 public feeds.</p>']
    for key, title in SECTION_DEFS:
        sec = s.get(key, {})
        blocks.append(f'<section class="baked-sec"><h2>{title}</h2>')
        items = sec.get("items") or []
        if items:
            blocks.extend(card(it) for it in items)
        else:
            blocks.append('<p>No headlines on this desk right now.</p>')
        blocks.append('</section>')
    ld_items = []
    for it in (s.get("top_world", {}).get("items") or []) + (s.get("wars", {}).get("items") or []):
        if it.get("url"):
            ld_items.append({"@type": "ListItem", "position": len(ld_items) + 1,
                             "name": it.get("title", ""), "url": it.get("url", "")})
    if ld_items:
        ld = json.dumps({"@context": "https://schema.org", "@type": "ItemList",
                         "name": "World news headlines right now — The World Desk",
                         "itemListElement": ld_items}).replace("</", "<\\/")
        blocks.append(f'<script type="application/ld+json">{ld}</script>')
    baked = "\n".join(blocks)
    idx = os.path.join(ROOT, "index.html")
    html = open(idx).read()
    a, b = "<!--WIRE:START-->", "<!--WIRE:END-->"
    if a in html and b in html:
        html = html[:html.index(a) + len(a)] + "\n" + baked + "\n" + html[html.index(b):]
        open(idx, "w").write(html)
    entries = []
    for it in (s.get("top_world", {}).get("items") or []):
        entries.append(f'<entry><title>{H.escape(it.get("title", ""))}</title>'
                       f'<link href="{H.escape(it.get("url", ""), quote=True)}"/>'
                       f'<id>{H.escape(it.get("url", ""), quote=True)}</id>'
                       f'<updated>{it.get("published") or gen}</updated>'
                       f'<summary>{H.escape(it.get("source", ""))} via The World Desk</summary></entry>')
    feed = ('<?xml version="1.0" encoding="utf-8"?>\n<feed xmlns="http://www.w3.org/2005/Atom">'
            f'<title>The World Desk — world news, wars, politics & policy, by Bishow Pandey</title>'
            f'<link href="{SITE}feed.xml" rel="self"/><link href="{SITE}"/>'
            f'<id>{SITE}</id><updated>{gen}</updated>' + "".join(entries) + "</feed>")
    open(os.path.join(ROOT, "feed.xml"), "w").write(feed)
    sm = ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
          f'<url><loc>{SITE}</loc><lastmod>{gen}</lastmod><changefreq>hourly</changefreq><priority>1.0</priority></url></urlset>')
    open(os.path.join(ROOT, "sitemap.xml"), "w").write(sm)
    print("  static: index.html baked, feed.xml + sitemap.xml regenerated")

if __name__ == "__main__":
    main()
