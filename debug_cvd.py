
import asyncio
import re
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.coinglass.com/cvd"
out, net, bodies = [], [], []
ROW_RX = re.compile(r"(\d{2}-\d{2}\s+\d{2}:\d{2})\s+(Bull|Bear)\s+div", re.I)


def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    out.append(line)


def flat(s):
    return " ".join((s or "").split())


async def on_resp(r):
    try:
        if r.request.resource_type not in ("xhr", "fetch"):
            return
        u = r.url
        net.append(f"{r.status} {u[:220]}")
        if re.search(r"diverg|cvd|signal", u, re.I):
            try:
                t = await r.text()
            except Exception:
                t = "<no body>"
            bodies.append(f"URL: {u}\nBODY: {t[:1500]}\n")
    except Exception:
        pass


async def seg(page, start, length=600):
    t = await page.locator("body").inner_text()
    i = t.find(start)
    return flat(t[i:i + length]) if i >= 0 else f"<'{start}' not found>"


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(
            args=["--disable-blink-features=AutomationControlled"]
        )
        ctx = await b.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 900},
            locale="en-US",
            timezone_id="Asia/Kolkata",
        )
        page = await ctx.new_page()
        page.on("response", lambda r: asyncio.create_task(on_resp(r)))

        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)
        for sel in ['button:has-text("Decline")', 'button:has-text("Accept")']:
            try:
                await page.locator(sel).first.click(timeout=2000)
                break
            except Exception:
                pass

        log("TITLE:", await page.title())
        log("URL:", page.url)
        log("LATEST (BTC):", await seg(page, "Latest divergence", 200))
        log("DIVS (BTC):", await seg(page, "Based on", 500))
        await page.screenshot(path="debug3_1_loaded.png")

        # ---- open coin picker ----
        try:
            log("BTC text count:", await page.get_by_text("BTC", exact=True).count())
            await page.get_by_text("BTC", exact=True).first.click(timeout=10000)
            await page.wait_for_timeout(1500)
            await page.screenshot(path="debug3_2_coin_open.png")

            inputs = page.locator("input:visible")
            for i in range(await inputs.count()):
                log("input", i,
                    await inputs.nth(i).get_attribute("placeholder"),
                    await inputs.nth(i).get_attribute("type"))

            box = page.locator(
                'input[type="search"]:visible, input[type="text"]:visible, input:not([type]):visible'
            ).first
            await box.fill("XAU", timeout=5000)
            await page.wait_for_timeout(2000)
            await page.screenshot(path="debug3_3_coin_search.png")

            for sel in ['[role="listbox"]', '[role="option"]']:
                c = await page.locator(sel).count()
                log(f"{sel} count:", c)
                if c:
                    html = await page.locator(sel).first.evaluate("e => e.outerHTML")
                    log("  HTML:", flat(html)[:700])

            opt = page.get_by_role("option", name=re.compile(r"XAU", re.I))
            if await opt.count() == 0:
                opt = page.get_by_text(re.compile(r"^XAU\b"))
            log("XAU option count:", await opt.count())
            await opt.first.click(timeout=8000)
            await page.wait_for_timeout(5000)
        except Exception as e:
            log("COIN SELECT ERROR:", str(e)[:250])

        log("URL after XAU:", page.url)
        body = await page.locator("body").inner_text()
        log("CVD title lines:", [l for l in body.split("\n") if "CVD" in l][:4])
        log("MARKETS:", re.findall(r"\d+/\d+ markets", body))
        await page.screenshot(path="debug3_4_xau.png")

        # ---- timeframes ----
        first_row_html_done = False
        for tf in ["1m", "5m", "15m"]:
            clicked = False
            for loc in (page.get_by_role("button", name=tf, exact=True),
                        page.get_by_text(tf, exact=True)):
                try:
                    if await loc.count():
                        await loc.first.click(timeout=5000)
                        clicked = True
                        break
                except Exception:
                    pass
            log(f"--- TF {tf} clicked={clicked} url={page.url}")
            await page.wait_for_timeout(3000)
            try:
                await page.get_by_text("Divergences", exact=True).first.click(timeout=5000)
            except Exception as e:
                log("Divergences tab error:", str(e)[:150])
            await page.wait_for_timeout(2500)

            log("SEG@3s:", await seg(page, "Based on", 500))
            await page.wait_for_timeout(3000)
            log("SEG@6s:", await seg(page, "Based on", 500))
            log("LATEST:", await seg(page, "Latest divergence", 160))

            text = await page.locator("body").inner_text()
            rows = ROW_RX.findall(text)
            log(f"ROWS found: {len(rows)} first3: {rows[:3]}")

            if not first_row_html_done:
                try:
                    html = await page.get_by_text(
                        re.compile(r"^(Bull|Bear) div$", re.I)
                    ).first.evaluate(
                        "e => { let n=e; for(let i=0;i<3&&n.parentElement;i++) n=n.parentElement; return n.outerHTML }"
                    )
                    log("ROW HTML:", flat(html)[:700])
                    first_row_html_done = True
                except Exception as e:
                    log("row html error:", str(e)[:150])

            await page.screenshot(path=f"debug3_tf_{tf}.png", full_page=True)

        Path("debug3_page.txt").write_text(
            (await page.locator("body").inner_text())[:30000]
        )
        await asyncio.sleep(1)
        Path("debug3_log.txt").write_text("\n".join(out))
        Path("debug3_network.txt").write_text("\n".join(dict.fromkeys(net)))
        Path("debug3_bodies.txt").write_text("\n".join(bodies))
        await b.close()


asyncio.run(main())
