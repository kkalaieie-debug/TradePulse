#!/usr/bin/env python3
"""TradePulse daily CPR & Pivot poster bot.

Fetches previous day / week / month High-Low-Close, builds the poster
(same design as the Poster Maker page), and posts it to a Telegram channel.

Usage:
    python bot.py            # fetch, render, post to Telegram
    python bot.py --no-post  # fetch + render only (saves poster.png)
    python bot.py --demo     # use sample data, no internet needed (saves poster.png)
"""
import base64
import html
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

HERE = Path(__file__).parent
IST = ZoneInfo("Asia/Kolkata")

# ---- settings (override with environment variables) -------------------------
# "Name|Yahoo symbol" pairs separated by commas. One poster is posted per index.
INDICES = os.getenv("INDICES", "NIFTY 50|^NSEI")
AUTHOR = os.getenv("AUTHOR", "Kalaivanan")
TAGLINE = os.getenv("TAGLINE", "Market Levels for Better Decisions")
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")  # e.g. @mychannel or -100123456789

COLORS = {"M": "#8b5cf6", "W": "#38bdf8", "D": "#34d399"}


# ---- data -------------------------------------------------------------------
def fetch_daily(symbol):
    """Return list of (date, high, low, close) sorted by date."""
    import yfinance as yf

    df = yf.Ticker(symbol).history(period="3mo", interval="1d", auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"No data returned for {symbol}")
    rows = []
    for ts, r in df.iterrows():
        rows.append((ts.date(), float(r["High"]), float(r["Low"]), float(r["Close"])))
    return sorted(rows)


def hlc(rows):
    return max(r[1] for r in rows), min(r[2] for r in rows), rows[-1][3]


def build_periods(rows, today):
    """Previous day, previous completed week, previous completed month."""
    past = [r for r in rows if r[0] < today]
    if not past:
        raise RuntimeError("No completed trading days found")

    # previous trading day
    d = past[-1]
    daily = dict(k="D", t="DAILY RANGE", s=f"{d[0].day} {d[0]:%B %Y}",
                 p="Previous day", H=d[1], L=d[2], C=d[3], u="day")

    # previous completed week (Mon-Fri before this week's Monday)
    monday = today - timedelta(days=today.weekday())
    wk_rows = [r for r in rows if r[0] < monday]
    last_wk = wk_rows[-1][0].isocalendar()[:2]
    wk = [r for r in wk_rows if r[0].isocalendar()[:2] == last_wk]
    a, b = wk[0][0], wk[-1][0]
    lbl = (f"{a.day}-{b.day} {b:%b %Y}" if a.month == b.month
           else f"{a.day} {a:%b} - {b.day} {b:%b %Y}")
    H, L, C = hlc(wk)
    weekly = dict(k="W", t="WEEKLY RANGE", s=lbl, p="Previous week",
                  H=H, L=L, C=C, u="week")

    # previous completed month
    first = today.replace(day=1)
    mo_rows = [r for r in rows if r[0] < first]
    ym = (mo_rows[-1][0].year, mo_rows[-1][0].month)
    mo = [r for r in mo_rows if (r[0].year, r[0].month) == ym]
    H, L, C = hlc(mo)
    monthly = dict(k="M", t="MONTHLY RANGE", s=f"{mo[-1][0]:%B %Y}",
                   p="Previous month", H=H, L=L, C=C, u="month")

    return [monthly, weekly, daily]


# ---- CPR / pivot maths (same as the Poster Maker) --------------------------------
def calc(H, L, C):
    P = (H + L + C) / 3
    m = (H + L) / 2
    o = 2 * P - m
    return dict(
        P=P, TC=max(m, o), BC=min(m, o),
        R1=2 * P - L, R2=P + H - L, R3=H + 2 * (P - L), R4=H + 3 * (P - L),
        S1=2 * P - H, S2=P - (H - L), S3=L - 2 * (H - P), S4=L - 3 * (H - P),
    )


def f2(v):
    return f"{v:,.2f}"


# ---- poster HTML ------------------------------------------------------------
def card(x):
    H, L, C = x["H"], x["L"], x["C"]
    c = COLORS[x["k"]]
    r = calc(H, L, C)
    w = r["TC"] - r["BC"]
    pc = w / r["P"] * 100
    if pc < 0.3:
        note = f"very narrow, trending {x['u']} possible"
    elif pc < 1:
        note = f"moderate width, balanced {x['u']} possible"
    else:
        note = f"wide, range-bound {x['u']} possible"

    mx = max(r["R4"] - r["TC"], r["BC"] - r["S4"])

    def bw(v):
        return max(8, abs(v) / mx * 100)

    def row(name, val, gap, ref, cls, arrow):
        return (f'<div class="lv"><div class="lr {cls}"><span class="ln">{name}</span>'
                f'<span class="bar"><i style="width:{bw(val - ref) * .6}%;background:currentColor"></i></span>'
                f'<span class="vl">{f2(val)}</span></div>'
                f'<div class="gp">{arrow} {f2(gap)} pts</div></div>')

    res = [("R4", r["R4"], r["R4"] - r["R3"]), ("R3", r["R3"], r["R3"] - r["R2"]),
           ("R2", r["R2"], r["R2"] - r["R1"]), ("R1", r["R1"], r["R1"] - r["TC"])]
    sup = [("S1", r["S1"], r["BC"] - r["S1"]), ("S2", r["S2"], r["S1"] - r["S2"]),
           ("S3", r["S3"], r["S2"] - r["S3"]), ("S4", r["S4"], r["S3"] - r["S4"])]
    res_html = "".join(row(n, v, g, r["TC"], "res", "▲") for n, v, g in res)
    sup_html = "".join(row(n, v, g, r["BC"], "sup", "▼") for n, v, g in sup)

    return f'''<div class="card" style="--c:{c}"><div class="ch"><b>{x["t"]}</b><span>{html.escape(x["s"])}</span></div>
<div class="pv">{x["p"]} &nbsp; High {f2(H)} &nbsp; Low {f2(L)}<br>Close {f2(C)}</div>
{res_html}
<div class="cpr"><div class="cr"><span>TC</span><span>{f2(r["TC"])}</span></div><div class="cr pv2"><span>PIVOT</span><span>{f2(r["P"])}</span></div><div class="cr"><span>BC</span><span>{f2(r["BC"])}</span></div><div class="cn">CPR width {f2(w)} pts &nbsp;|&nbsp; {note}</div></div>
{sup_html}
<div class="ft"><b>{html.escape(AUTHOR)}</b><div>▲ gap up from TC to R4 &nbsp; ▼ gap down from BC to S4</div><div>For information only. Not investment advice.</div></div></div>'''


def poster_html(index_name, periods):
    logo = base64.b64encode((HERE / "logo.jpg").read_bytes()).decode()
    css = (HERE / "poster.css").read_text()
    cards = "".join(card(p) for p in periods)
    return f'''<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{{box-sizing:border-box}}body{{margin:0;background:#050813}}
{css}
#poster{{transform:none}}
</style></head><body><div id="poster">
<div class="hd"><img src="data:image/jpeg;base64,{logo}"><div class="ht"><div class="t1">{html.escape(index_name)}</div><div class="t2">CPR &amp; PIVOT LEVELS</div><div class="t3">Learn · Analyze · Trade</div></div><div class="tag">{html.escape(TAGLINE)}</div></div>
<div class="cols">{cards}</div>
<div class="bt"><div><b>IDENTIFY KEY LEVELS</b>CPR | Pivot | S1 R1</div><div><b>PLAN YOUR TRADES</b>Buy on Strength<br>Sell on Weakness</div><div><b>MANAGE RISK</b>Protect Capital<br>Trade with Discipline</div><div class="br">TradePulse</div></div>
<div class="bm">STOCK MARKET EDUCATION | TRADING | RESEARCH</div>
</div></body></html>'''


def render_png(html_text, out_path):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1024, "height": 1200}, device_scale_factor=2)
        page.set_content(html_text, wait_until="load")
        page.locator("#poster").screenshot(path=str(out_path))
        browser.close()


# ---- telegram ---------------------------------------------------------------
def post_telegram(png_path, caption):
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}"
    with open(png_path, "rb") as f:
        r = requests.post(f"{url}/sendPhoto", data={"chat_id": CHAT_ID, "caption": caption},
                          files={"photo": f}, timeout=60)
    r.raise_for_status()
    print("Posted:", r.json().get("ok"))


# ---- main -------------------------------------------------------------------
DEMO = [
    dict(k="M", t="MONTHLY RANGE", s="September 2026", p="Previous month",
         H=24143.15, L=22569.65, C=22620.45, u="month"),
    dict(k="W", t="WEEKLY RANGE", s="19-23 Oct 2026", p="Previous week",
         H=23080.25, L=22217.30, C=22421.95, u="week"),
    dict(k="D", t="DAILY RANGE", s="24 October 2026", p="Previous day",
         H=22610.60, L=22217.30, C=22421.95, u="day"),
]


def main():
    args = set(sys.argv[1:])
    today = datetime.now(IST).date()

    if today.weekday() >= 5 and "--demo" not in args and not os.getenv("FORCE"):
        print("Weekend - nothing to post.")
        return

    for item in INDICES.split(","):
        name, _, symbol = item.strip().partition("|")
        periods = DEMO if "--demo" in args else build_periods(fetch_daily(symbol), today)
        out = HERE / (f"poster_{name.replace(' ', '_')}.png")
        render_png(poster_html(name, periods), out)
        print("Saved", out)

        if "--no-post" in args or "--demo" in args:
            continue
        caption = f"{name} - CPR & Pivot levels for {today:%d %b %Y}\n#{name.replace(' ', '')} #CPR #Pivot"
        post_telegram(out, caption)


if __name__ == "__main__":
    main()
