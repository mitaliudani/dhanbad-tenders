// ==UserScript==
// @name        GeM Sab Pages
// @namespace   dhanbad-tenders
// @version     1.1
// @description Saare pages ek saath Sort Machine mein
// @include     https://bidplus.gem.gov.in/*
// @match       https://bidplus.gem.gov.in/*
// @grant       none
// ==/UserScript==
window.__GEMGRAB_BUTTON = true;

(function () {
  "use strict";
  if (window.__GEMGRAB_BUTTON) { window.__gemGrabRun = run; return; }
  run();
  function run() {
  if (window.__gemGrab) { return; }
  window.__gemGrab = true;
  var SORT = window.__GEMSORT_URL || "https://mitaliudani.github.io/dhanbad-tenders/sort.html";
  var REB = /GEM\/\d{4}\/B\/\d{5,9}/i, RE_ALL = /GEM\/\d{4}\/[BR]\/\d{5,9}/gi;
  var PAUSE = window.__GEMGRAB_PAUSE || 1500, MAX_PAGES = 60;
  var all = {}, order = [];

  var box = document.createElement("div");
  box.style.cssText = "position:fixed;left:8px;right:8px;bottom:8px;z-index:2147483647;background:#0D6A70;color:#fff;" +
    "font:15px/1.4 system-ui,sans-serif;padding:12px 14px;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,.35)";
  document.body.appendChild(box);
  function say(t) { box.textContent = t; }
  function stop(t) { say(t); window.__gemGrab = false; setTimeout(function () { box.remove(); }, 9000); }

  function clean(s) { return String(s || "").replace(/\s+/g, " ").trim(); }
  function field(text, label) {
    var stops = "BID NO|RA NO|Items?|Quantity|Department Name And Address|Start Date|End Date|View Corrigendum";
    var m = text.match(new RegExp("(?:" + label + ")\\s*:\\s*([\\s\\S]*?)(?=(?:" + stops + ")\\s*[:/]|$)", "i"));
    return m ? clean(m[1]) : "";
  }

  
  function readPage() {
    var found = [], seen = {};
    var walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null);
    var n;
    while ((n = walker.nextNode())) {
      var m = n.nodeValue.match(REB);
      if (!m || box.contains(n)) continue;
      var bid = m[0].toUpperCase();
      if (seen[bid]) continue;
      var el = n.parentElement, card = el;
      while (el && el !== document.body) {
        var t = el.innerText || el.textContent || "";
        var bs = {}; (t.match(/GEM\/\d{4}\/B\/\d{5,9}/gi) || []).forEach(function (x) { bs[x.toUpperCase()] = 1; });
        if (Object.keys(bs).length > 1) break;
        card = el;
        if (/End Date/i.test(t) && /Items?\s*:/i.test(t)) break;
        el = el.parentElement;
      }
      var text = card.innerText || card.textContent || "";
      if (!/End Date/i.test(text)) continue;
      seen[bid] = 1;
      var full = "";
      [].forEach.call(card.querySelectorAll("[data-content],[title]"), function (x) {
        var v = clean(x.getAttribute("data-content") || x.getAttribute("title"));
        if (v.length > full.length && !REB.test(v)) full = v;
      });
      var pdf = "";
      [].forEach.call(card.querySelectorAll("a[href]"), function (a) {
        if (!pdf && /showbidDocument|showradocumentPdf/i.test(a.getAttribute("href"))) pdf = a.href;
      });
      var ra = ((text.match(RE_ALL) || []).filter(function (x) { return /\/R\//i.test(x); })[0] || "").toUpperCase();
      var items = field(text, "Items?");
      found.push({
        bid: bid, ra: ra,
        items: full && full.length >= items.replace(/\.\.\.$/, "").length ? full : items,
        qty: field(text, "Quantity"),
        dept: field(text, "Department Name And Address"),
        start: field(text, "Start Date"),
        end: field(text, "End Date"),
        pdf: pdf
      });
    }
    return found;
  }

  function nextButton() {
    
    var els = [].slice.call(document.querySelectorAll("a, button")).concat([].slice.call(document.querySelectorAll("li, span")));
    for (var i = 0; i < els.length; i++) {
      var e = els[i], t = clean(e.textContent);
      if (!/^(next|next\s*[›»>]|[›»])$/i.test(t)) continue;
      var dis = e.disabled || e.getAttribute("aria-disabled") === "true" ||
        /disabled/i.test(e.className) || (e.parentElement && /disabled/i.test(e.parentElement.className));
      if (dis || !e.offsetParent) continue;
      return e;
    }
    return null;
  }

  function totalPages() {
    var m = (document.body.innerText || "").match(/of\s+([\d,]+)\s+records/i);
    return m ? Math.ceil(parseInt(m[1].replace(/,/g, ""), 10) / 10) : 0;
  }

  function firstBid() { var r = readPage(); return r.length ? r[0].bid : ""; }

  var page = 0, pages = totalPages();
  function step() {
    page++;
    var got = readPage();
    got.forEach(function (b) { if (!all[b.bid]) { all[b.bid] = b; order.push(b.bid); } });
    say("GeM se bids padh rahe hain… page " + page + (pages ? " / " + pages : "") + " · ab tak " + order.length + " bids");
    var nb = nextButton();
    if (!nb || page >= MAX_PAGES || (pages && page >= pages)) return finish();
    var before = firstBid();
    nb.click();
    var waited = 0;
    (function wait() {
      setTimeout(function () {
        waited += 300;
        var now = firstBid();
        if (now && now !== before) return setTimeout(step, PAUSE);
        if (waited > 20000) return finish("Agla page 20 second mein nahi khula, isliye yahin tak ke bids le rahe hain.");
        wait();
      }, 300);
    })();
  }

  function finish(note) {
    if (!order.length) return stop("Is page pe GeM ke bids nahi mile. Pehle GeM Advance Search mein Dhanbad ki list kholiye, phir ye button dabaiye.");
    say((note ? note + " " : "") + order.length + " bids mil gaye. Sort Machine khul rahi hai…");
    var data = order.map(function (k) { return all[k]; });
    var json = JSON.stringify(data);
    var b64 = btoa(unescape(encodeURIComponent(json))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
    setTimeout(function () { location.href = SORT + "#gem=" + b64; }, 800);
  }

  say("Shuru kar rahe hain…");
  setTimeout(step, 300);
  }
})();

(function () {
  var b = document.createElement("button");
  b.textContent = "⬇ Saare pages → Sort Machine";
  b.style.cssText = "position:fixed;right:10px;bottom:10px;z-index:2147483646;background:#0D6A70;color:#fff;border:0;" +
    "border-radius:999px;padding:12px 16px;font:600 15px system-ui,sans-serif;box-shadow:0 4px 14px rgba(0,0,0,.3)";
  b.addEventListener("click", function () { b.remove(); window.__gemGrabRun(); });
  document.body.appendChild(b);
})();
