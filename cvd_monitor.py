
import os
import re
import json
import asyncio
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.coinglass.com/cvd?symbol=XAU"
STATE_FILE = Path("cvd_state.json")

TIMEFRAMES = ["1m", "5m", "15m"]   # add "30m" here for a 4th tab
MAX_ALERTS_PER_TF = 5              # avoid spam when 1m fires many signals
KEEP_SEEN = 60
BLOCKED = {"image", "media", "font"}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# rows of the Divergences tab:  "10-31 20:14 Bear div 77,493.8 -0.08%"
ROW_RX = re.compile(r"(\d{2}-\d{2} \d{2}:\d{2}) (Bull|Bear) div ([\d,]+(?:\.\d+)?)", re.I)
# top card:  "Latest divergence · Combined Bull div 1d ago Confirmed 10-30 10:45 · 4,017.3"
CARD_RX = re.compile(
    r"Latest divergence · \w+ (Bull|Bear) div .{1,20}? Confirmed "
    r"(\d{2}-\d{2} \d{2}:\d{2}) · ([\d,]+(?:\.\d+)?)", re.I)
# chart label:  "XAU · 15m"
TF_RX = re.compile(r"XAU · (\d+[mhdMHD])\b")


# ---------- Telegram ----------

def clean(value):
    return (value or "").strip().strip('"').strip("'").strip()


def get_chat_id(token):
    url = f"https://api.telegram.org/bot{token}/getUpdates"
    with urllib.request.urlopen(url, timeout=20) as r:
        data = json.loads(r.read())
    for upd in reversed(data.get("result", [])):
        msg = upd.get("message") or upd.get("channel_post") or {}
        if "chat" in msg:
            return str(msg["chat"]["id"])
    return None


def send_telegram(token, chat_id, text):
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        raise RuntimeError(f"Telegram HTTP {e.code}: {body}")


# ---------- Parsing ----------

def flat(s):
    return " ".join((s or "").split())


def parse_state(text):
    """-> (timeframe label, latest-card row or None, table rows newest first)"""
    m = TF_RX.search(text)
    tf = m.group(1) if m else None

    card = None
    m = CARD_RX.search(text)
    if m:
        card = {"type": m.group(1).capitalize(), "time": m.group(2), "price": m.group(3)}

    rows = []
    i = text.find("Based on")
    if i >= 0:
        j = text.find("CVD by exchange", i)
        seg = text[i:j] if j > i else text[i:i + 6000]
        seen = set()
        for tm, t, p in ROW_RX.findall(seg):
            key = (tm, t.capitalize())
            if key in seen:
                continue
            seen.add(key)
            rows.append({"type": t.capitalize(), "time": tm, "price": p})
    return tf, card, rows


def row_key(r):
    return f"{r['time']}|{r['type']}"


def compute_alerts(state, data):
    first = state is None
    state = dict(state or {})
    alerts, baseline, notes = [], [], {}
    for tf in TIMEFRAMES:
        rows = data.get(tf)
        if rows is None:
            continue  # scrape failed: keep old state
        keys = [row_key(r) for r in rows]
        if tf not in state:
            state[tf] = {"seen": keys[:KEEP_SEEN]}
            baseline.append(tf)
            continue
        seen = state[tf]["seen"]
        seen_set = set(seen)
        new = [r for r in rows if row_key(r) not in seen_set]   # newest first
        if new:
            send = new[:MAX_ALERTS_PER_TF]
            if len(new) > len(send):
                notes[tf] = len(new) - len(send)
            for r in reversed(send):                            # oldest first
                alerts.append((tf, r))
            merged = [row_key(r) for r in new] + seen
            state[tf] = {"seen": list(dict.fromkeys(merged))[:KEEP_SEEN]}
    return alerts, baseline, state, notes, first


# ---------- Browser helpers ----------

async def block_heavy(route):
    if route.request.resource_type in BLOCKED:
        await route.abort()
    else:
        await route.continue_()


async def page_text(page):
    return flat(await page.locator("body").inner_text())


async def dismiss_cookies(page):
    for sel in ['button:has-text("Decline")', 'button:has-text("Accept")']:
        try:
            await page.locator(sel).first.click(timeout=1000)
            return
        except Exception:
            pass


async def click_visible(page, text):
    """Click the first VISIBLE element whose full text is `text`
    (the page also has hidden duplicates, which broke the debug run)."""
    loc = page.get_by_text(text, exact=True)
    for i in range(await loc.count()):
        el = loc.nth(i)
        try:
            if not await el.is_visible():
                continue
            try:
                await el.click(timeout=3000)
            except Exception:
                await el.dispatch_event("click")
            return True
        except Exception:
            continue
    return False


async def wait_for_text(page, text, secs):
    for _ in range(int(secs * 2)):
        if text in await page_text(page):
            return True
        await page.wait_for_timeout(500)
    return False


async def wait_label(page, tf, secs=10):
    for _ in range(int(secs * 2)):
        label, _, _ = parse_state(await page_text(page))
        if label and label.lower() == tf.lower():
            return True
        await page.wait_for_timeout(500)
    raise RuntimeError(f"chart label never changed to {tf}")


async def stabilize(page, min_s=2.0, max_s=20.0, quiet=6):
    """Wait until card + table text stops changing (data finished loading)."""
    loop = asyncio.get_event_loop()
    start, last, same = loop.time(), None, 0
    while loop.time() - start < max_s:
        await page.wait_for_timeout(500)
        _, card, rows = parse_state(await page_text(page))
        sig = json.dumps([card, rows[:3]])
        same = same + 1 if sig == last else 0
        last = sig
        if same >= quiet and loop.time() - start >= min_s:
            return


async def scrape_tf(browser, sem, tf):
    async with sem:
        for attempt in (1, 2):
            ctx = await browser.new_context(
                user_agent=UA,
                viewport={"width": 1366, "height": 900},
                locale="en-US",
                timezone_id="Asia/Kolkata",
            )
            await ctx.route("**/*", block_heavy)
            page = await ctx.new_page()

            intervals = set()

            def on_resp(r):
                if "kline" in r.url:
                    m = re.search(r"interval=(\w+)", r.url)
                    if m:
                        intervals.add(m.group(1))

            page.on("response", on_resp)
            try:
                await page.goto(URL, wait_until="domcontentloaded", timeout=30000)
                await dismiss_cookies(page)
                try:
                    await page.wait_for_function(
                        "() => /Latest divergence[\\s\\S]{0,120}Confirmed/.test(document.body.innerText)",
                        timeout=25000,
                    )
                except Exception:
                    print(f"{tf}: 'Latest divergence' card not loaded yet")
                await stabilize(page, min_s=2.0)

                label, _, _ = parse_state(await page_text(page))
                if not label or label.lower() != tf.lower():
                    before = set(intervals)
                    if not await click_visible(page, tf):
                        raise RuntimeError(f"button {tf} not clickable")
                    await wait_label(page, tf)
                    for _ in range(20):                      # wait for new candles
                        if intervals - before:
                            break
                        await page.wait_for_timeout(500)
                    await stabilize(page, min_s=2.5)

                if await click_visible(page, "Divergences"):
                    await wait_for_text(page, "Based on", 8)
                    await stabilize(page, min_s=1.5)
                else:
                    print(f"{tf}: Divergences tab not clickable, using top card only")

                label, card, rows = parse_state(await page_text(page))
                if not label or label.lower() != tf.lower():
                    raise RuntimeError(f"page shows {label}, expected {tf}")
                if not rows and card:
                    rows = [card]
                print(f"{tf}: {len(rows)} rows, latest: {rows[0] if rows else 'none'}")
                return rows
            except Exception as e:
                print(f"{tf}: attempt {attempt} FAILED -> {e}")
                try:
                    await page.screenshot(path=f"debug_cvd_{tf}_{attempt}.png")
                    t = await page_text(page)
                    i = t.find("Latest divergence")
                    print(f"{tf}: page says: {t[i:i + 200] if i >= 0 else t[:200]}")
                except Exception:
                    pass
            finally:
                await ctx.close()
        return None


# ---------- Main ----------

async def main():
    token = clean(os.environ.get("TELEGRAM_BOT_TOKEN"))
    if token.lower().startswith("bot"):
        token = token[3:]
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")
    chat_id = clean(os.environ.get("TELEGRAM_CHAT_ID")) or get_chat_id(token)
    if not chat_id:
        raise RuntimeError("No chat ID. Send any message to your bot, then run again.")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            args=["--disable-blink-features=AutomationControlled"]
        )
        try:
            sem = asyncio.Semaphore(len(TIMEFRAMES))
            results = await asyncio.gather(*[scrape_tf(browser, sem, tf) for tf in TIMEFRAMES])
        finally:
            await browser.close()

    data = {tf: r for tf, r in zip(TIMEFRAMES, results) if r is not None}
    if not data:
        raise RuntimeError("No CVD data found for any timeframe.")

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else None
    alerts, baseline, new_state, notes, first = compute_alerts(state, data)

    if first:
        lines = []
        for tf in TIMEFRAMES:
            rows = data.get(tf)
            if rows:
                r = rows[0]
                lines.append(f"{tf}: {r['type']} div {r['time']} @ {r['price']}")
            elif rows == []:
                lines.append(f"{tf}: no divergence yet")
        send_telegram(token, chat_id, "✅ XAU CVD divergence monitor started\n" + "\n".join(lines))
        print("Initial snapshot saved.")
    else:
        for tf in baseline:
            print(f"{tf}: new timeframe, baseline saved (no alert)")
        for tf, r in alerts:
            bull = r["type"] == "Bull"
            msg = (
                f"{'🟢' if bull else '🔴'} XAU CVD {r['type']} divergence ({tf})\n"
                f"Time (CoinGlass): {r['time']}\n"
                f"Price: {r['price']}\n"
                f"Based on Combined CVD\n"
                f"Source: CoinGlass CVD"
            )
            print("Telegram:", send_telegram(token, chat_id, msg))
        for tf, n in notes.items():
            send_telegram(token, chat_id, f"ℹ️ XAU CVD {tf}: {n} older new signals not shown")

    STATE_FILE.write_text(json.dumps(new_state, indent=2))
    print("State saved.")


if __name__ == "__main__":
    asyncio.run(main())
