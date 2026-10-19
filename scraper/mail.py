#!/usr/bin/env python3
"""Dhanbad GeM bids from tender alert emails.

Opens the alert Gmail (read-only), finds emails that carry GeM bid numbers
(GEM/2026/B/1234567) - GeM's own seller alerts, a paid tender site's alerts, or
anything forwarded there - and turns each bid into an entry in docs/bids.json.
Only bids that mention Dhanbad district are shown on the website.

Needs two GitHub secrets: GMAIL_ADDRESS and GMAIL_APP_PASSWORD (a Google
"app password", not the normal password). Emails are never changed or marked
read, and their text is never printed or saved; only the bid facts are.
"""
import collections
import datetime as dt
import email
import imaplib
import os
import re
import sys
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

BIDS_FILE = os.path.join(C.ROOT, "docs", "bids.json")
STATUS_FILE = os.path.join(C.ROOT, "docs", "status.json")
STATE_FILE = os.path.join(C.ROOT, "data", "mail_state.json")

ADDRESS = os.environ.get("GMAIL_ADDRESS", "").strip()
PASSWORD = re.sub(r"\s+", "", os.environ.get("GMAIL_APP_PASSWORD", ""))
FIRST_DAYS = 60      # first run looks back this far
NORMAL_DAYS = 5      # later runs look at recent mail only (re-reading is harmless)
KEEP_DAYS = 60
MAX_MESSAGES = 400
YEARS = " OR ".join('"GEM/%d"' % y for y in range(C.TODAY.year - 1, C.TODAY.year + 3))
QUERY = "(from:gem.gov.in OR from:gov.in OR %s)" % YEARS

LOCAL_WORDS = [
    "dhanbad", "bccl", "bharat coking", "mugma", "sindri", "jharia", "katras", "gomoh", "maithon", "panchet",
    "indian school of mines", "iit (ism)", "iit ism", "iit-ism", "cimfr", "dgms", "koyla nagar", "koyla bhawan",
    "nirsa", "chirkunda", "kusunda", "bastacolla", "govindpur", "sijua", "lodna", "barora", "moonidih", "tundi",
    "baghmara", "topchanchi", "baliapur", "hirapur", "bank more", "bankmore", "saraidhela", "putki", "loyabad",
    "jealgora", "ekra", "digwadih", "barwa road", "chasnalla", "jitpur", "kumardhubi", "kaliasole", "egarkund",
    "rajganj", "muraidih", "tetulmari", "bhowra", "dhansar", "pathardih", "sudamdih", "kenduadih", "mahuda",
    "snmmch", "pmch dhanbad", "dhn division",
]
LOCAL_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(w) for w in LOCAL_WORDS) + r")(?![a-z])", re.I)
LOCAL_PIN = re.compile(r"(?<!\d)82[68]\s?\d{3}(?!\d)")
CORR_RE = re.compile(r"corrigendum|extension|extended|amendment|date changed|revised", re.I)
END_RE = re.compile(r"(bid\s*end|end\s*date|closing|close[sd]?\s*(?:on|date)?|due\s*date|last\s*date|end\s*time|"
                    r"bid\s*submission\s*end|deadline)[^0-9a-z]{0,40}([^\n]{0,60})", re.I)
TITLE_RE = re.compile(r"(?:^|\n)\s*(?:items?|item\s*name|item\s*category|product(?:\s*name)?|description|bid\s*title|"
                      r"title|work|category)\s*[:\-]\s*([^\n]{4,200})", re.I)
BUYER_RE = re.compile(r"(?:^|\n)\s*(?:organi[sz]ation(?:\s*name)?|buyer(?:\s*organi[sz]ation)?|department(?:\s*name)?|"
                      r"office(?:\s*name)?)\s*[:\-]\s*([^\n]{3,160})", re.I)
MINISTRY_RE = re.compile(r"(?:^|\n)\s*ministry(?:\s*/\s*state(?:\s*name)?)?\s*[:\-]\s*([^\n]{3,120})", re.I)
QTY_RE = re.compile(r"(?:^|\n)\s*(?:total\s*)?quantity\s*[:\-]\s*([\d,]+)", re.I)


# ---------------------------------------------------------------- reading mail
def header_text(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def bodies(msg):
    plain, html = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            raw = part.get_payload(decode=True) or b""
            text = raw.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        (plain if ctype == "text/plain" else html).append(text)
    return "\n".join(plain), "\n".join(html)


def html_text(h):
    soup = BeautifulSoup(h, "html.parser")
    for t in soup(["script", "style", "head"]):
        t.decompose()
    return soup.get_text("\n", strip=True)


def unique_bids(s):
    out = []
    for b in C.BID_RE.findall(s or ""):
        b = b.upper()
        if b not in out:
            out.append(b)
    return out


def contexts_from_html(h):
    """For digest emails: the smallest HTML block around each bid number that holds no other bid."""
    soup = BeautifulSoup(h, "html.parser")
    for t in soup(["script", "style", "head"]):
        t.decompose()
    ctx = {}
    for node in soup.find_all(string=C.BID_RE):
        bid = C.BID_RE.search(node).group(0).upper()
        el = best = node.parent
        while el is not None and el.name not in ("body", "html", "[document]"):
            if len(unique_bids(el.get_text(" "))) > 1:
                break
            best, el = el, el.parent
        ctx[bid] = ctx.get(bid, "") + "\n" + best.get_text("\n", strip=True)
    return ctx


def contexts_from_text(t):
    ctx = {}
    for para in re.split(r"\n\s*\n", t):
        here = unique_bids(para)
        if len(here) == 1:
            ctx[here[0]] = ctx.get(here[0], "") + "\n" + para
        elif len(here) > 1:
            lines = para.split("\n")
            for i, line in enumerate(lines):
                one = unique_bids(line)
                if len(one) != 1:
                    continue
                block = [line]
                j = i + 1
                while j < len(lines) and j <= i + 5 and not unique_bids(lines[j]):
                    block.append(lines[j])
                    j += 1
                k = i - 1
                while k >= 0 and k >= i - 2 and not unique_bids(lines[k]):
                    block.insert(0, lines[k])
                    k -= 1
                ctx[one[0]] = ctx.get(one[0], "") + "\n" + "\n".join(block)
    return ctx


def find_end(c):
    for m in END_RE.finditer(c):
        d = C.parse_date(m.group(2))
        if d:
            return C.ymd(d), C.parse_time(m.group(2))
    return "", ""


ORG_RE = re.compile(r"\b(limited|ltd|corporation|department|ministry|institute|university|railway|council|"
                    r"authority|board|nigam|parishad|office of|directorate|commission|company)\b", re.I)
REFER_RE = re.compile(r"refer\s*doc\.?\s*([^\n]+)", re.I)


def from_refer_doc(c):
    """Card digests write items as 'Refer doc <title>;<qty>;<items>' or 'Refer doc <qty>;<title>'."""
    m = REFER_RE.search(c)
    if not m:
        return "", ""
    fields = [C.clean(x) for x in m.group(1).split(";") if C.clean(x)]
    nums = [f for f in fields if re.fullmatch(r"[\d,]+", f)]
    words = [f for f in fields if not re.fullmatch(r"[\d,]+", f)]
    title = words[0] if words else ""
    if len(words) > 1 and len(title) < 60:
        title = title + " - " + words[1]
    return title[:200], (nums[0].replace(",", "") if nums else "")


def first_line(c):
    for raw in c.split("\n"):
        l = re.sub(r"^\s*(\d+[).]|[-•*#]+)\s*", "", C.clean(raw))
        l = re.sub(r"^corrigendum\s*[:\-–]\s*", "", l, flags=re.I)
        if len(l) < 6 or C.BID_RE.search(l):
            continue
        if re.match(r"(bid|organi[sz]ation|buyer|closing|last date|end date|quantity|consignee|location|ministry)\b", l, re.I):
            continue
        if ORG_RE.search(l) and len(l) < 80:
            continue
        return l[:160]
    return ""


def tidy_subject(s):
    s = re.sub(r"^\s*((re|fw|fwd)\s*:\s*)+", "", s or "", flags=re.I)
    s = C.BID_RE.sub("", s)
    return C.clean(s).strip(" -:|")[:160]


def parse_message(subject, text, html):
    full = subject + "\n" + text
    bids = unique_bids(full + "\n" + (html_text(html) if html else ""))
    if not bids:
        return []
    if len(bids) == 1:
        ctx = {bids[0]: full + ("\n" + html_text(html) if html and len(text) < 200 else "")}
    else:
        ctx = contexts_from_html(html) if html else {}
        if len(ctx) < len(bids) and text:
            for k, v in contexts_from_text(text).items():
                ctx.setdefault(k, v)
    out = []
    for bid in bids:
        c = ctx.get(bid, "")
        title = ""
        m = None
        for m in TITLE_RE.finditer(c):
            t = C.clean(m.group(1))
            if len(re.sub(r"[^a-z]", "", C.BID_RE.sub("", t), flags=re.I)) >= 4:
                title = t[:200]
                break
        refer_title, refer_qty = from_refer_doc(c)
        if not title and refer_title:
            title = refer_title
        if not title and len(bids) > 1:
            title = first_line(c)
        if not title:
            title = tidy_subject(subject)
        end, end_time = find_end(c)
        buyer = BUYER_RE.search(c)
        if not buyer and len(bids) > 1:
            org = next((C.clean(x) for x in c.split("\n") if ORG_RE.search(x) and 4 < len(C.clean(x)) < 80
                        and not C.BID_RE.search(x)), "")
            buyer = re.match(r"(.+)", org) if org else None
        ministry = MINISTRY_RE.search(c)
        qty = QTY_RE.search(c)
        out.append({
            "bid_no": bid,
            "title": title or bid,
            "end_date": end,
            "end_time": end_time,
            "buyer": C.clean(buyer.group(1))[:120] if buyer else "",
            "ministry": C.clean(ministry.group(1))[:100] if ministry else "",
            "total_qty": qty.group(1).replace(",", "") if qty else refer_qty,
            "local": bool(LOCAL_RE.search(c) or LOCAL_PIN.search(c)),
            "corr": bool(CORR_RE.search(c)),
            "context": c,
        })
    return out


def fetch_messages(days):
    imap = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=60)
    imap.login(ADDRESS, PASSWORD)
    box = '"[Gmail]/All Mail"'
    typ, _ = imap.select(box, readonly=True)
    if typ != "OK":
        imap.select("INBOX", readonly=True)
    imap.literal = ("newer_than:%dd %s" % (days, QUERY)).encode("utf-8")
    typ, data = imap.uid("SEARCH", "CHARSET", "UTF-8", "X-GM-RAW")
    uids = (data[0] or b"").split() if typ == "OK" else []
    uids = uids[-MAX_MESSAGES:]
    msgs = []
    for uid in uids:
        typ, parts = imap.uid("FETCH", uid, "(BODY.PEEK[])")
        if typ != "OK":
            continue
        raw = next((p[1] for p in parts if isinstance(p, tuple)), None)
        if raw:
            msgs.append(email.message_from_bytes(raw))
    try:
        imap.logout()
    except Exception:
        pass
    return msgs


# ---------------------------------------------------------------- merging
def to_record(hit, when, sender):
    rec = {
        "bid_no": hit["bid_no"],
        "title": hit["title"],
        "end_date": hit["end_date"],
        "end_time": hit["end_time"],
        "buyer": hit["buyer"],
        "ministry": hit["ministry"],
        "total_qty": hit["total_qty"],
        "first_seen": C.ymd(when),
        "updated": C.ymd(when),
        "local": hit["local"],
        "corrigendum": hit["corr"],
        "corrigendum_note": "The alert email mentions a corrigendum or extension. Check the bid on GeM." if hit["corr"] else "",
        "status_note": "",
        "source": "Email alert from " + sender,
        "links": [],
        "pdf_url": "",
        "kind": "goods",
    }
    parts = [p for p in re.split(r"\s*,\s*", hit["title"]) if len(p) > 2][:20] or [hit["title"]]
    rec["items"] = [{"name": p[:200], "qty": "", "spec": ""} for p in parts]
    if len(rec["items"]) == 1 and rec["total_qty"]:
        rec["items"][0]["qty"] = rec["total_qty"]
    probe = dict(rec, buyer=(hit["buyer"] + " " + hit["context"][:800]))
    rec["group"], rec["area"], ministry = C.classify(probe)
    if rec["group"] not in {g for _, g, _ in C.GROUPS}:
        rec["group"] = hit["buyer"] or "Other"
    rec["ministry"] = C.classify(dict(rec))[2] or ministry
    if not rec["buyer"]:
        rec["buyer"] = rec["group"]
    s = "%s invited bids for %s." % (rec["buyer"], rec["title"])
    if rec["end_date"]:
        s += " Closes %s%s." % (C.fmt_day(rec["end_date"]), (" at " + rec["end_time"]) if rec["end_time"] else "")
    rec["summary"] = s
    return rec


def merge(bids, hit, when, sender, stats):
    old = bids.get(hit["bid_no"])
    if not old:
        bids[hit["bid_no"]] = to_record(hit, when, sender)
        stats["new"] += 1
        return
    day = C.ymd(when)
    if hit["local"] and old.get("local") is False:
        old["local"] = True
    if hit["end_date"] and old.get("end_date") and hit["end_date"] != old["end_date"] and day >= old.get("updated", ""):
        old["corrigendum"] = True
        old["corrigendum_note"] = "Closing date changed from %s to %s (email of %s). Check the bid on GeM." % (
            C.fmt_day(old["end_date"]), C.fmt_day(hit["end_date"]), C.fmt_day(day))
        old["end_date"], old["end_time"] = hit["end_date"], hit["end_time"] or old.get("end_time", "")
        old["updated"] = day
        stats["changed"] += 1
    elif hit["corr"] and not old.get("corrigendum"):
        old["corrigendum"] = True
        old["corrigendum_note"] = "Corrigendum mentioned in an alert email of %s. Check the bid on GeM." % C.fmt_day(day)
        old["updated"] = max(day, old.get("updated", ""))
        stats["changed"] += 1
    for k in ("end_date", "end_time", "buyer", "ministry", "total_qty"):
        if not old.get(k) and hit.get(k):
            old[k] = hit[k]
    if hit["title"] and (not old.get("title") or old["title"] == old["bid_no"]):
        old["title"] = hit["title"]


def main():
    now = C.NOW.isoformat(timespec="seconds")
    data = C.load_json(BIDS_FILE, {"generated_at": "", "bids": []})
    bids = {b["bid_no"]: b for b in data.get("bids", []) if b.get("bid_no")}
    state = C.load_json(STATE_FILE, {})
    old_status = C.load_json(STATUS_FILE, {})
    stats = collections.Counter()
    problems = []
    ok = False
    if not ADDRESS or not PASSWORD:
        problems.append("Gmail is not connected yet: add the GMAIL_ADDRESS and GMAIL_APP_PASSWORD secrets.")
    else:
        days = NORMAL_DAYS if state.get("backfilled") else FIRST_DAYS
        try:
            msgs = fetch_messages(days)
            ok = True
        except imaplib.IMAP4.error as e:
            msgs = []
            problems.append("Gmail refused the login. Check the app password. (%s)" % str(e)[:80])
        except Exception as e:  # network trouble
            msgs = []
            problems.append("Could not reach Gmail: %s" % type(e).__name__)
        stats["emails"] = len(msgs)
        dated = []
        for m in msgs:
            try:
                when = parsedate_to_datetime(m.get("Date")).astimezone(C.IST).date()
            except Exception:
                when = C.TODAY
            dated.append((when, m))
        dated.sort(key=lambda x: x[0])
        for when, m in dated:
            subject = header_text(m.get("Subject"))
            sender = (parseaddr(header_text(m.get("From")))[1].split("@")[-1] or "email").lower()
            text, html = bodies(m)
            if not text and html:
                text = html_text(html)
            for hit in parse_message(subject, text, html):
                stats["bids_in_emails"] += 1
                merge(bids, hit, when, sender, stats)
        if ok:
            state["backfilled"] = True
            state["last_ok"] = now

    cutoff = C.TODAY - dt.timedelta(days=KEEP_DAYS)
    before = len(bids)
    bids = {k: v for k, v in bids.items() if C.keep(v, cutoff)}
    stats["dropped_old"] = before - len(bids)
    out = sorted(bids.values(), key=lambda b: (b.get("end_date") or "9999", b["bid_no"]))
    shown = sum(1 for b in out if b.get("local") is not False)
    C.save_json(BIDS_FILE, {"generated_at": now if ok else data.get("generated_at", ""), "bids": out})
    C.save_json(STATE_FILE, state)
    status = {
        "last_run": now,
        "last_success": now if ok else old_status.get("last_success", ""),
        "ok": ok,
        "source": "email",
        "bids_in_file": len(out),
        "dhanbad_bids": shown,
        "counts": dict(stats),
        "problems": problems,
    }
    C.save_json(STATUS_FILE, status)
    print({k: v for k, v in status.items() if k != "problems"})
    for p in problems:
        print("PROBLEM:", p)


if __name__ == "__main__":
    main()
