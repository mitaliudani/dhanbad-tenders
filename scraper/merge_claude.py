#!/usr/bin/env python3
"""Merge a bids.json made by the Claude refresh task into docs/bids.json.

Usage: python scraper/merge_claude.py /path/to/claude_bids.json

The Claude task reads the public tender listings twice a day (Tender247 allows
Claude's agents in its robots.txt) and passes its full list here. Bids that came
from alert emails are kept; when both sources know a bid, the Claude data wins
for every field it fills, and a corrigendum mark is never removed.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

BIDS_FILE = os.path.join(C.ROOT, "docs", "bids.json")


def main(path):
    with open(path, encoding="utf-8") as f:
        incoming = [b for b in json.load(f).get("bids", []) if b.get("bid_no")]
    if not incoming:
        sys.exit("No bids in %s - nothing merged, site left as it was." % path)
    data = C.load_json(BIDS_FILE, {"generated_at": "", "bids": []})
    bids = {b["bid_no"]: b for b in data.get("bids", []) if b.get("bid_no")}
    added = updated = 0
    for b in incoming:
        old = bids.get(b["bid_no"])
        if not old:
            bids[b["bid_no"]] = b
            added += 1
            continue
        corr = bool(old.get("corrigendum") or b.get("corrigendum"))
        note = b.get("corrigendum_note") or old.get("corrigendum_note", "")
        for k, v in b.items():
            if v not in ("", None, [], {}):
                old[k] = v
        old["corrigendum"], old["corrigendum_note"] = corr, note if corr else ""
        old["local"] = True
        updated += 1
    out = sorted(bids.values(), key=lambda x: (x.get("end_date") or "9999", x["bid_no"]))
    C.save_json(BIDS_FILE, {"generated_at": C.NOW.isoformat(timespec="seconds"), "bids": out})
    print("Merged: %d new, %d updated, %d bids in the site now." % (added, updated, len(out)))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
