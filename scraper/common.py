"""Shared helpers: dates, files, and sorting buyers into groups and areas."""
import datetime as dt
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
NOW = dt.datetime.now(IST)
TODAY = NOW.date()
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
BID_RE = re.compile(r"GEM/\d{4}/[BR]/\d{5,9}", re.I)


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
    (r"government of jharkhand|govt\.? of jharkhand|jharkhand govt|zila parishad|nagar nigam|municipal corporation|"
     r"deputy commissioner|\bdrda\b|district (?:administration|office)", "Jharkhand Govt", "Govt of Jharkhand"),
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
        group = (rec.get("buyer") or "Other").split("\n")[0][:60].strip() or "Other"
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


def keep(rec, cutoff):
    for k in ("end_date", "start_date", "first_seen"):
        d = parse_date(rec.get(k, ""))
        if d and d >= cutoff:
            return True
    return False
