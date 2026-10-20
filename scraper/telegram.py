#!/usr/bin/env python3
"""Telegram alerts for the Dhanbad tender website.

Needs one GitHub secret: TELEGRAM_BOT_TOKEN (from @BotFather). Anyone who opens
the bot and presses Start gets a message for every new Dhanbad bid that reaches
docs/bids.json, and for every new corrigendum. Send /stop to the bot to leave.

Files: data/telegram_chats.json (who gets alerts), data/telegram_sent.json (what
was already announced, so nothing is sent twice).
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
SITE = "https://mitaliudani.github.io/dhanbad-tenders/"
BIDS_FILE = os.path.join(C.ROOT, "docs", "bids.json")
CHATS_FILE = os.path.join(C.ROOT, "data", "telegram_chats.json")
SENT_FILE = os.path.join(C.ROOT, "data", "telegram_sent.json")
MAX_SINGLE = 8          # more new bids than this in one run are sent as one summary


def api(method, **params):
    url = "https://api.telegram.org/bot%s/%s" % (TOKEN, method)
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(url, data=data, timeout=30) as r:
        out = json.loads(r.read().decode())
    if not out.get("ok"):
        raise RuntimeError(out.get("description", "Telegram error"))
    return out["result"]


def send(chat, text):
    try:
        api("sendMessage", chat_id=chat, text=text, disable_web_page_preview="true")
        time.sleep(0.1)
        return True
    except Exception as e:  # blocked bot, deleted chat, network
        print("Could not message a chat:", type(e).__name__, str(e)[:80])
        return False


def is_shown(b):
    return b.get("local") is not False and b.get("kind") != "services"


def is_open(b):
    d = C.parse_date(b.get("end_date", ""))
    return d is None or d >= C.TODAY


def when(b):
    d = C.fmt_day(b.get("end_date", ""))
    return (d + (" " + b["end_time"] if b.get("end_time") else "")) if d else "not stated"


def bid_text(b, head):
    lines = [head, b.get("title") or b["bid_no"]]
    who = b.get("buyer") if b.get("group") in ("Jharkhand Govt", "Others", "") else b.get("group")
    lines.append(" · ".join(x for x in (who, b.get("area")) if x))
    lines.append("Closes: " + when(b))
    if b.get("emd"):
        lines.append("EMD: " + b["emd"])
    lines.append("Bid no: " + b["bid_no"])
    if b.get("corrigendum") and b.get("corrigendum_note"):
        lines.append("Corrigendum: " + b["corrigendum_note"])
    lines.append("Bid PDF: " + b["pdf_url"] if b.get("pdf_url") else "Search on GeM: https://bidplus.gem.gov.in/all-bids")
    return "\n".join(l for l in lines if l)


def main():
    if not TOKEN:
        print("TELEGRAM_BOT_TOKEN not set; Telegram alerts are off.")
        return
    bids = [b for b in C.load_json(BIDS_FILE, {}).get("bids", []) if b.get("bid_no") and is_shown(b)]
    chats = C.load_json(CHATS_FILE, {"chats": {}, "offset": 0})
    sent = C.load_json(SENT_FILE, {})
    first_time = not sent

    # 1. people who pressed Start (or /stop)
    try:
        updates = api("getUpdates", offset=chats.get("offset", 0), timeout=0)
    except Exception as e:
        print("Telegram not reachable or token wrong:", str(e)[:100])
        return
    for u in updates:
        chats["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        chat = msg.get("chat", {})
        cid, text = str(chat.get("id", "")), (msg.get("text") or "").strip().lower()
        if not cid:
            continue
        if text.startswith("/stop"):
            if chats["chats"].pop(cid, None) is not None:
                send(cid, "Theek hai, ab alerts nahi aayenge. Wapas chahiye to /start bhejiye.")
            continue
        if cid not in chats["chats"]:
            chats["chats"][cid] = {"since": C.ymd(C.TODAY)}   # no names: this repository is public
            open_now = sum(1 for b in bids if is_open(b))
            send(cid, "Namaste! Ab Dhanbad ke har naye GeM goods bid ka alert yahan aayega.\n"
                      "Abhi %d bids open hain. Poori list: %s\nAlerts band karne ke liye /stop bhejiye." % (open_now, SITE))
        elif text.startswith("/list") or text.startswith("/start"):
            soon = sorted((b for b in bids if is_open(b)), key=lambda b: b.get("end_date") or "9999")[:10]
            body = "\n".join("• %s – %s (%s)" % (when(b), (b.get("title") or "")[:70], b["bid_no"]) for b in soon)
            send(cid, "Sabse pehle band hone wale 10 bids:\n%s\n\nPoori list: %s" % (body or "abhi koi open bid nahi", SITE))

    # 2. new bids and new corrigenda since the last run
    new, corr = [], []
    for b in bids:
        prev = sent.get(b["bid_no"])
        if prev is None:
            if is_open(b):
                new.append(b)
            sent[b["bid_no"]] = {"corr": bool(b.get("corrigendum")), "end": b.get("end_date", "")}
        elif (bool(b.get("corrigendum")) and not prev.get("corr")) or (prev.get("end") and b.get("end_date") and prev["end"] != b["end_date"]):
            if is_open(b):
                corr.append(b)
            sent[b["bid_no"]] = {"corr": bool(b.get("corrigendum")), "end": b.get("end_date", "")}

    if first_time:
        new, corr = [], []          # the first run only learns what already exists; it does not flood the chat
    targets = list(chats["chats"])
    if targets and (new or corr):
        msgs = []
        if len(new) > MAX_SINGLE:
            body = "\n".join("• %s – %s" % (when(b), (b.get("title") or "")[:70]) for b in new[:25])
            msgs.append("🆕 %d naye Dhanbad GeM bids:\n%s\n\nPoori list aur PDF: %s" % (len(new), body, SITE))
        else:
            msgs += [bid_text(b, "🆕 Naya Dhanbad GeM bid") for b in new]
        msgs += [bid_text(b, "✏️ Corrigendum / date badli") for b in corr[:MAX_SINGLE]]
        for cid in targets:
            for m in msgs:
                send(cid, m)
    print("Telegram: %d chats, %d new, %d corrigenda announced." % (len(targets), len(new), len(corr)))

    keep_from = C.ymd(C.TODAY - dt.timedelta(days=90))
    current = {b["bid_no"] for b in bids}
    sent = {k: v for k, v in sent.items() if k in current or (v.get("end") or "9999") >= keep_from}
    C.save_json(CHATS_FILE, chats)
    C.save_json(SENT_FILE, sent)


if __name__ == "__main__":
    main()
