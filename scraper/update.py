#!/usr/bin/env python3
"""Dhanbad GeM goods bids.

Reads the public Dhanbad GeM listing on Tender247, opens each new bid's public
page there to get its GeM bid number, then reads that bid's public page on
Tenderlelo for the details and the official GeM bid PDF link. Writes
docs/bids.json, which the website (docs/index.html) shows.

Polite by design: it checks each site's robots.txt before every request, says
who it is in its User-Agent, waits between requests, and only opens a bid's
pages once (or again when the listing shows it was updated).
"""
import collections
import datetime as dt
import json
import os
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIDS_FILE = os.path.join(ROOT, "docs", "bids.json")
STATUS_FILE = os.path.join(ROOT, "docs", "status.json")
SEEN_FILE = os.path.join(ROOT, "data", "seen.json")
DEBUG_DIR = os.path.join(ROOT, "debug")

LIST_URL = "https://www.tender247.com/gem-tenders/city-tenders/dhanbad-tenders"
MAX_PAGES = 6            # listing pages per run (about 50 bids each)
MAX_NEW = 90             # new bids opened per run; the rest wait for the next run
MAX_REFRESH = 20         # known bids re-read per run when the listing says they changed
KEEP_DAYS = 60           # bids closed longer ago than this are dropped
DELAY = 2.5              # seconds between two requests to the same website
REPO = os.environ.get("GITHUB_REPOSITORY", "")
UA = ("DhanbadTenderBoard/1.0 (personal, low-volume reader of public listing pages; "
      + ("+https://github.com/" + REPO if REPO else "runs a few times a day") + ")")
AGENT_TOKEN = "dhanbadtenderboard"

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime.now(IST)
TODAY = NOW.date()

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
BID_RE = re.compile(r"GEM/\d{4}/[BR]/\d{5,9}", re.I)
T247_HREF = re.compile(r"/tender-details/([a-z0-9-]+?)-(\d{6,})(?=$|[/?#])", re.I)
SERVICE_RE = re.compile(
    r"\b(services?|hiring|hire of|manpower|outsourc\w*|filling of|excavat\w*|construction|civil works?|"
    r"works? contract|transportation|cleaning|housekeeping|security guards?|catering|consultan\w*|"
    r"survey(?:ing)?|repair(?:ing)? and maintenance|annual maintenance|\bamc\b|operation and maintenance|"
    r"labour|insurance|audit|training programme|event management|painting work)\b", re.I)


# ---------------------------------------------------------------- small helpers
def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def ymd(d):
    return d.isoformat() if d else ""


def parse_date(s):
    """Accepts 29-10-2026, 29/10/2026, 2026-10-29, 6 Nov 2026, Nov 6, 2026."""
    s = clean(s)
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", s)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b", s)
        if m:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        else:
            m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?[\s-]+([A-Za-z]{3,9})[,\s-]+(\d{4})\b", s)
            if m and m.group(2)[:3].lower() in MONTHS:
                d, mo, y = int(m.group(1)), MONTHS[m.group(2)[:3].lower()], int(m.group(3))
            else:
                m = re.search(r"\b([A-Za-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})\b", s)
                if m and m.group(1)[:3].lower() in MONTHS:
                    d, mo, y = int(m.group(2)), MONTHS[m.group(1)[:3].lower()], int(m.group(3))
                else:
                    return None
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None


def parse_time(s):
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?", s or "", re.I)
    if not m:
        return ""
    h, mi = int(m.group(1)), int(m.group(2))
    ap = (m.group(3) or "").lower().replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return "%02d:%02d" % (h, mi) if h < 24 and mi < 60 else ""


def date_after(label_re, text):
    m = re.search(label_re + r"\s*(?:on|date)?\s*:?\s*([0-9]{1,2}[-/.][0-9]{1,2}[-/.][0-9]{4}|[0-9]{1,2}\s+[A-Za-z]{3,9},?\s+[0-9]{4})",
                  text, re.I)
    return parse_date(m.group(1)) if m else None


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def save_debug(name, text):
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        with open(os.path.join(DEBUG_DIR, name), "w", encoding="utf-8") as f:
            f.write(text or "")
    except OSError:
        pass


# ---------------------------------------------------------------- robots.txt
class Robots:
    """robots.txt check with * and $ wildcards; the longest matching rule wins."""

    def __init__(self, session):
        self.session = session
        self.rules = {}

    def allowed(self, url):
        p = urlparse(url)
        host = p.scheme + "://" + p.netloc
        if host not in self.rules:
            self.rules[host] = self._load(host)
        rules = self.rules[host]
        if rules is None:
            return False
        path = (p.path or "/") + ("?" + p.query if p.query else "")
        best = None
        for allow, pat in rules:
            if self._match(pat, path):
                if best is None or len(pat) > best[0] or (len(pat) == best[0] and allow):
                    best = (len(pat), allow)
        return True if best is None else best[1]

    @staticmethod
    def _match(pat, path):
        anchored = pat.endswith("$")
        if anchored:
            pat = pat[:-1]
        rx = "^" + ".*".join(re.escape(x) for x in pat.split("*")) + ("$" if anchored else "")
        return re.match(rx, path) is not None

    def _load(self, host):
        try:
            r = self.session.get(host + "/robots.txt", timeout=20)
        except requests.RequestException:
            return None                      # cannot read the rules: do not crawl
        if r.status_code >= 500:
            return None
        if r.status_code >= 400:
            return []                        # no robots.txt: everything allowed
        groups, agents, rules, last_was_rule = [], [], [], False
        for raw in r.text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            key, val = [x.strip() for x in line.split(":", 1)]
            key = key.lower()
            if key == "user-agent":
                if last_was_rule:
                    groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(val.lower())
                last_was_rule = False
            elif key in ("allow", "disallow"):
                if val:
                    rules.append((key == "allow", val))
                last_was_rule = True
        if agents:
            groups.append((agents, rules))
        mine = [r_ for a, r_ in groups if any(x != "*" and x in AGENT_TOKEN for x in a)]
        if not mine:
            mine = [r_ for a, r_ in groups if "*" in a]
        return [rule for rs in mine for rule in rs]


# ---------------------------------------------------------------- fetching
class Fetcher:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml",
                               "Accept-Language": "en-IN,en;q=0.9"})
        self.robots = Robots(self.s)
        self.last = {}
        self.count = 0
        self.errors = []
        self.blocked = set()

    def get(self, url):
        host = urlparse(url).netloc
        if host in self.blocked:
            return None
        if not self.robots.allowed(url):
            self.errors.append("robots.txt does not allow " + url)
            return None
        err = ""
        for attempt in range(2):
            wait = DELAY - (time.time() - self.last.get(host, 0))
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.s.get(url, timeout=40)
                self.last[host] = time.time()
                self.count += 1
                if r.status_code == 200:
                    if "cf-chl" in r.text[:5000] or "Just a moment..." in r.text[:3000]:
                        err = "Blocked by a bot check (Cloudflare) at " + host
                        self.blocked.add(host)
                        break
                    return r.text
                if r.status_code == 404:
                    return None
                err = "HTTP %d at %s" % (r.status_code, url)
                if r.status_code in (401, 403):
                    self.blocked.add(host)
                    err = "HTTP %d (refused) at %s" % (r.status_code, host)
                    break
                if r.status_code == 429:
                    time.sleep(30)
            except requests.RequestException as e:
                err = "%s at %s" % (type(e).__name__, url)
            time.sleep(4)
        if err:
            self.errors.append(err)
        return None


# ---------------------------------------------------------------- Tender247
def tidy_t247_title(t):
    t = clean(t)
    t = re.sub(r"T247\s*ID\s*-?\s*\d+", "", t, flags=re.I)
    t = re.sub(r"^\s*supply\s+of\s*-?\s*", "", t, flags=re.I)
    t = re.split(r"\s*\|\s*quantity\s*-", t, flags=re.I)[0]
    t = clean(t).strip(" -|,")
    return (t[:1].upper() + t[1:]) if t else ""


def parse_t247_list(html, base):
    soup = BeautifulSoup(html, "html.parser")
    anchors = []
    for a in soup.find_all("a", href=True):
        m = T247_HREF.search(a["href"])
        if m:
            anchors.append((a, m))
    out = collections.OrderedDict()
    for a, m in anchors:
        tid = m.group(2)
        if tid in out:
            continue
        node = card = a
        for _ in range(10):
            par = node.parent
            if par is None or par.name in ("body", "html", "[document]"):
                break
            ids = {x.group(2) for x in (T247_HREF.search(h["href"]) for h in par.find_all("a", href=True)) if x}
            if len(ids) > 1:
                break
            node = card = par
        text = clean(card.get_text(" ", strip=True))
        names = [clean(x.get_text(" ", strip=True)) for x, mm in anchors
                 if mm.group(2) == tid and (x is a or card in x.parents)]
        raw_title = max(names, key=len) if names else ""
        if len(raw_title) < 12:
            raw_title = m.group(1).replace("-", " ")
        qty = re.search(r"\|\s*quantity\s*-\s*([\d,]+)", text, re.I)
        out[tid] = {
            "t247_id": tid,
            "url": urljoin(base, a["href"]),
            "raw_title": raw_title,
            "title": tidy_t247_title(raw_title),
            "closes": ymd(date_after(r"Closes?", text) or date_after(r"Closing", text)),
            "updated": ymd(date_after(r"Updated", text)),
            "corrigendum": bool(re.search(r"\bcorrigendum\b", text, re.I)),
            "qty": qty.group(1).replace(",", "") if qty else "",
            "dhanbad": "dhanbad" in text.lower(),
        }
    return out


def parse_t247_detail(html):
    text = BeautifulSoup(html, "html.parser").get_text("\n", strip=True)
    m = re.search(r"(?:Tender\s*No\.?|Organi[sz]ation\s+Tender\s+ID)\s*:?\s*(GEM/\d{4}/[BR]/\d{5,9})", text, re.I)
    if m:
        bid = m.group(1).upper()
    else:
        found = collections.Counter(x.upper() for x in BID_RE.findall(html))
        bid = found.most_common(1)[0][0] if found else ""
    return {
        "bid_no": bid,
        "end_date": ymd(date_after(r"Submission\s+Date", text)),
    }


def looks_like_service(title):
    t = (title or "").lower()
    if t.startswith("supply"):
        return False
    return bool(SERVICE_RE.search(t))


# ---------------------------------------------------------------- Tenderlelo
def tenderlelo_url(bid):
    return "https://www.tenderlelo.com/tender/" + bid.lower().replace("/", "-") + "/"


def parse_tenderlelo(html, bid):
    if bid.lower() not in html.lower():
        return None
    pdf = re.search(r"https?://bidplus\.gem\.gov\.in/showbidDocument/(\d+)", html)
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg"]):
        t.decompose()
    h1 = soup.find("h1")
    title = clean(h1.get_text(" ", strip=True)) if h1 else ""
    lines = [clean(x) for x in soup.get_text("\n").split("\n")]
    lines = [x for x in lines if x]
    low = [x.lower().rstrip(" :") for x in lines]

    def val(*labels):
        for lab in labels:
            for i, l in enumerate(low):
                if l == lab and i + 1 < len(lines):
                    return lines[i + 1]
                if l.startswith(lab + ":") or l.startswith(lab + " :"):
                    return clean(lines[i].split(":", 1)[1])
        return ""

    full = "\n".join(lines)
    cons = ""
    m = re.search(r"\b(8[23]\d{4})\s*,\s*([^\n]{3,140})", full)
    if m:
        cons = m.group(1) + ", " + clean(m.group(2))
    closes = val("closes", "bid end date/time", "bid end date", "end date")
    published = val("published", "bid start date/time", "bid start date", "start date")
    return {
        "title": title,
        "pdf_url": "https://bidplus.gem.gov.in/showbidDocument/" + pdf.group(1) if pdf else "",
        "ministry": val("ministry", "ministry/state name", "ministry / state name"),
        "buyer": val("organisation", "organization", "organisation name", "organization name"),
        "department": val("department", "department name"),
        "office": val("office", "office name"),
        "delivers": val("delivers to", "location"),
        "start_date": ymd(parse_date(published)),
        "start_time": parse_time(published),
        "end_date": ymd(parse_date(closes)),
        "end_time": parse_time(closes),
        "bid_type": val("bid type"),
        "total_qty": re.sub(r"[^\d]", "", val("total quantity")),
        "est_value": val("estimated value", "estimated bid value"),
        "emd": val("emd", "emd amount"),
        "mse": val("mse", "mse exemption"),
        "consignee": cons,
    }


# ---------------------------------------------------------------- buyer groups
GROUPS = [
    (r"bharat coking|\bbccl\b", "BCCL", "Ministry of Coal"),
    (r"eastern coal ?fields|\becl\b", "ECL Mugma", "Ministry of Coal"),
    (r"central mine planning|\bcmpdi", "CMPDI", "Ministry of Coal"),
    (r"railway|\becr\b|east central", "Railways (ECR)", "Ministry of Railways"),
    (r"indian institute of technology|indian school of mines|\biit\b|\bism\b", "IIT (ISM)", "Ministry of Education"),
    (r"hindustan urvarak|\bhurl\b", "HURL Sindri", "Ministry of Chemicals & Fertilizers"),
    (r"damodar valley|\bdvc\b", "DVC", "Ministry of Power"),
    (r"mines safety|\bdgms\b", "DGMS", "Ministry of Labour & Employment"),
    (r"cimfr|central institute of mining|\bcsir\b", "CSIR-CIMFR", "Ministry of Science & Technology"),
    (r"steel authority|\bsail\b", "SAIL Collieries", "Ministry of Steel"),
    (r"coal india", "Coal India", "Ministry of Coal"),
    (r"jharkhand", "Jharkhand Govt", "Govt of Jharkhand"),
]
AREAS = [
    (r"mugma regional workshop", "Mugma Regional Workshop"), (r"mugma", "Mugma Area"),
    (r"central hospital", "Central Hospital"), (r"kusunda", "Kusunda Area"),
    (r"govindpur", "Govindpur Area"), (r"sijua", "Sijua Area"), (r"\bc\.?v\.? area|\bcv area", "CV Area"),
    (r"bastacolla", "Bastacolla Area"), (r"katras", "Katras Area"), (r"\bp\.?b\.? area|pootkee|putki", "PB Area"),
    (r"barora", "Barora Area"), (r"lodna", "Lodna Area"), (r"western jharia|\bwj area", "Western Jharia Area"),
    (r"\beast jharia|\bej area", "East Jharia Area"), (r"block[- ]?ii", "Block-II Area"),
    (r"ekra", "Central Store, Ekra"), (r"jealgora", "Central Stores, Jealgora"),
    (r"koyla nagar|koyla bhawan", "Koyla Nagar"), (r"hirapur", "Hirapur"), (r"sindri", "Sindri"),
    (r"gomoh", "Gomoh"), (r"barwa ?road", "Barwa Road"), (r"digwadih", "Digwadih"),
    (r"chasnalla", "Chasnalla"), (r"maithon", "Maithon"), (r"panchet", "Panchet"),
    (r"nirsa", "Nirsa"), (r"chirkunda", "Chirkunda"), (r"bhowra", "Bhowra"), (r"moonidih", "Moonidih"),
]
GROUP_DEFAULT_AREA = {"IIT (ISM)": "IIT (ISM) Dhanbad", "Railways (ECR)": "Dhanbad Division",
                      "HURL Sindri": "Sindri", "DGMS": "DGMS HQ, Hirapur", "CSIR-CIMFR": "CIMFR Dhanbad",
                      "BCCL": "BCCL (area not stated)"}


def classify(rec):
    who = " ".join([rec.get("buyer", ""), rec.get("department", ""), rec.get("ministry", "")]).lower()
    group, ministry = "", ""
    for rx, g, mi in GROUPS:
        if re.search(rx, who):
            group, ministry = g, mi
            break
    if not group:
        group = rec.get("buyer") or "Other"
    where = " ".join([rec.get("consignee", ""), rec.get("office", ""), rec.get("delivers", ""), rec.get("title", "")]).lower()
    area = ""
    for rx, a in AREAS:
        if re.search(rx, where):
            area = a
            break
    area = area or GROUP_DEFAULT_AREA.get(group, "Dhanbad")
    m = clean(rec.get("ministry", ""))
    if m:
        m = re.sub(r"\bAnd\b|\band\b", "&", m.title()).replace(" Of ", " of ")
        if re.search(r"jharkhand", m, re.I) and "Ministry" not in m:
            m = "Govt of Jharkhand"
    return group, area, m or ministry or ""


def fmt_day(s):
    d = parse_date(s)
    return "%d %s %d" % (d.day, d.strftime("%b"), d.year) if d else ""


def build_record(bid, entry, tl, today):
    title = (tl or {}).get("title") or entry.get("title") or bid
    rec = {
        "bid_no": bid,
        "t247_id": entry.get("t247_id", ""),
        "title": title[:400],
        "first_seen": ymd(today),
        "updated": ymd(today),
        "status_note": "",
        "corrigendum": bool(entry.get("corrigendum")),
        "corrigendum_note": "Corrigendum issued (Tender247 listing)" if entry.get("corrigendum") else "",
        "links": [{"label": "Tender247", "url": entry["url"]}] if entry.get("url") else [],
    }
    for k in ("pdf_url", "ministry", "buyer", "department", "office", "delivers", "start_date", "start_time",
              "end_date", "end_time", "bid_type", "total_qty", "est_value", "emd", "mse", "consignee"):
        rec[k] = (tl or {}).get(k, "")
    if tl:
        rec["links"].append({"label": "Tenderlelo", "url": tenderlelo_url(bid)})
    rec["end_date"] = rec["end_date"] or entry.get("closes", "")
    rec["total_qty"] = rec["total_qty"] or entry.get("qty", "")
    parts = [x for x in re.split(r"\s+,\s+", title) if x.strip()] if tl else []
    if not parts:
        parts = [x for x in re.split(r"\s*,\s*", entry.get("title", "")) if x.strip()][:12] or [title]
    rec["items"] = [{"name": clean(p)[:200], "qty": "", "spec": ""} for p in parts[:25]]
    if len(rec["items"]) == 1 and rec["total_qty"]:
        rec["items"][0]["qty"] = rec["total_qty"]
    rec["group"], rec["area"], rec["ministry"] = classify(rec)
    if not rec["buyer"]:
        rec["buyer"] = rec["group"]
    s = "%s invited bids for %s." % (rec["buyer"], title if len(title) < 160 else title[:157] + "…")
    if rec["total_qty"]:
        s += " Total quantity %s." % rec["total_qty"]
    if rec["end_date"]:
        s += " Closes %s%s." % (fmt_day(rec["end_date"]), (" at " + rec["end_time"]) if rec.get("end_time") else "")
    rec["summary"] = s
    rec["kind"] = "goods"
    return rec


def refresh_record(rec, tl, entry, today):
    """Re-read details for a known bid; mark a corrigendum if the closing date moved."""
    old_end = rec.get("end_date", "")
    if not tl:
        note_corr(rec, old_end, entry.get("closes", ""), entry, today)
        if entry.get("closes"):
            rec["end_date"] = entry["closes"]
        return
    new = build_record(rec["bid_no"], entry, tl, today)
    for k, v in new.items():
        if k in ("first_seen", "links", "corrigendum", "corrigendum_note"):
            continue
        if v:
            rec[k] = v
    note_corr(rec, old_end, rec.get("end_date", ""), entry, today)


def note_corr(rec, old_end, new_end, entry, today):
    if old_end and new_end and old_end != new_end:
        rec["corrigendum"] = True
        rec["corrigendum_note"] = "Closing date changed from %s to %s (noticed %s). Check the bid PDF on GeM." % (
            fmt_day(old_end), fmt_day(new_end), fmt_day(ymd(today)))
        rec["updated"] = ymd(today)
    elif entry.get("corrigendum") and not rec.get("corrigendum"):
        rec["corrigendum"] = True
        rec["corrigendum_note"] = "Corrigendum issued (Tender247 listing, noticed %s). Check the bid PDF on GeM." % fmt_day(ymd(today))
        rec["updated"] = ymd(today)


def add_link(rec, label, url):
    links = rec.setdefault("links", [])
    if url and not any(l.get("label") == label for l in links):
        links.append({"label": label, "url": url})


def keep(rec, cutoff):
    for k in ("end_date", "start_date", "first_seen"):
        d = parse_date(rec.get(k, ""))
        if d and d >= cutoff:
            return True
    return False


# ---------------------------------------------------------------- main
def main():
    today = TODAY
    cutoff = today - dt.timedelta(days=KEEP_DAYS)
    data = load_json(BIDS_FILE, {"generated_at": "", "bids": []})
    bids = {b["bid_no"]: b for b in data.get("bids", []) if b.get("bid_no")}
    seen = load_json(SEEN_FILE, {})
    old_status = load_json(STATUS_FILE, {})
    f = Fetcher()
    stats = collections.Counter()

    # 1. listing pages
    listing = collections.OrderedDict()
    pages_ok = 0
    for page in range(1, MAX_PAGES + 1):
        url = LIST_URL if page == 1 else LIST_URL + "?page=%d" % page
        html = f.get(url)
        if not html:
            break
        if page == 1:
            save_debug("list_page1.html", html)
        got = parse_t247_list(html, url)
        fresh = [k for k in got if k not in listing]
        pages_ok += 1
        for k in fresh:
            listing[k] = got[k]
        if not fresh:
            break
    stats["listed"] = len(listing)
    print("Listing pages read: %d, bids listed: %d" % (pages_ok, len(listing)))

    # 2. each listed bid
    new_budget, refresh_budget = MAX_NEW, MAX_REFRESH
    saved_detail = saved_tl = False
    for tid, e in listing.items():
        info = seen.get(tid)
        if info and info.get("skip"):
            continue
        bid = (info or {}).get("bid", "")
        if not bid:
            if looks_like_service(e["raw_title"]):
                seen[tid] = {"bid": "", "skip": "services", "date": ymd(today)}
                stats["services_skipped"] += 1
                continue
            if new_budget <= 0:
                stats["waiting"] += 1
                continue
            new_budget -= 1
            html = f.get(e["url"])
            if not html:
                continue
            if not saved_detail:
                save_debug("t247_detail.html", html)
                saved_detail = True
            d = parse_t247_detail(html)
            bid = d["bid_no"]
            if not bid:
                seen[tid] = {"bid": "", "skip": "no GeM number", "date": ymd(today)}
                continue
            e["closes"] = e["closes"] or d["end_date"]
            seen[tid] = {"bid": bid, "date": ymd(today), "updated": e["updated"]}
        rec = bids.get(bid)
        if rec:
            rec["t247_id"] = tid
            add_link(rec, "Tender247", e["url"])
            changed = e["updated"] and seen[tid].get("updated") and e["updated"] != seen[tid].get("updated")
            if (changed or not rec.get("pdf_url")) and refresh_budget > 0:
                refresh_budget -= 1
                html = f.get(tenderlelo_url(bid))
                tl = parse_tenderlelo(html, bid) if html else None
                refresh_record(rec, tl, e, today)
                stats["refreshed"] += 1
            else:
                note_corr(rec, rec.get("end_date", ""), e["closes"], e, today)
                if e["closes"] and e["closes"] != rec.get("end_date"):
                    rec["end_date"] = e["closes"]
            seen[tid]["updated"] = e["updated"] or seen[tid].get("updated", "")
            seen[tid]["date"] = ymd(today)
            continue
        if new_budget <= 0 and info:
            stats["waiting"] += 1
            continue
        if info:
            new_budget -= 1
        html = f.get(tenderlelo_url(bid))
        tl = parse_tenderlelo(html, bid) if html else None
        if html and not saved_tl:
            save_debug("tenderlelo.html", html)
            saved_tl = True
        rec = build_record(bid, e, tl, today)
        if (tl and re.search(r"\bservices?\b|\bhiring\b|\bmanpower\b", tl.get("title", ""), re.I)
                and not e["raw_title"].lower().startswith("supply")):
            seen[tid]["skip"] = "services"
            stats["services_skipped"] += 1
            continue
        bids[bid] = rec
        stats["new"] += 1
        print("  new:", bid, "|", rec["title"][:70])

    # 3. tidy up and save
    before = len(bids)
    bids = {k: v for k, v in bids.items() if keep(v, cutoff)}
    stats["dropped_old"] = before - len(bids)
    seen = {k: v for k, v in seen.items()
            if (parse_date(v.get("date", "")) or today) >= today - dt.timedelta(days=KEEP_DAYS + 60)}

    ok = pages_ok > 0
    out = sorted(bids.values(), key=lambda b: (b.get("end_date") or "9999", b["bid_no"]))
    stamp = NOW.isoformat(timespec="seconds")
    save_json(BIDS_FILE, {"generated_at": stamp if ok else data.get("generated_at", ""), "bids": out})
    save_json(SEEN_FILE, seen)
    status = {
        "last_run": stamp,
        "last_success": stamp if ok else old_status.get("last_success", ""),
        "ok": ok,
        "listing_pages": pages_ok,
        "requests": f.count,
        "bids_in_file": len(out),
        "counts": dict(stats),
        "problems": f.errors[:12],
    }
    save_json(STATUS_FILE, status)
    print(json.dumps(status, indent=1))
    if not ok:
        print("WARNING: could not read the Tender247 listing. Old data kept.", file=sys.stderr)


if __name__ == "__main__":
    main()
