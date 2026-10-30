
import asyncio
from pathlib import Path
from playwright.async_api import async_playwright

URL = "https://www.coinglass.com/liquidation-levels"
out = []

def log(*a):
    line = " ".join(str(x) for x in a)
    print(line)
    out.append(line)

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(
            args=["--disable-blink-features=AutomationControlled"]
        )
        ctx = await b.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 900},
            locale="en-US",
        )
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(8000)

        # close cookie banner
        for sel in ['button:has-text("Decline")', 'button:has-text("Accept")']:
            try:
                await page.locator(sel).first.click(timeout=2000)
                break
            except Exception:
                pass
        await page.wait_for_timeout(1000)

        # open the BTCUSDT dropdown by its text
        trigger = page.get_by_text("BTCUSDT", exact=True).first
        log("trigger count:", await page.get_by_text("BTCUSDT", exact=True).count())
        await trigger.click(timeout=10000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path="debug2_1_open.png")

        inputs = page.locator("input:visible")
        log("visible inputs:", await inputs.count())
        for i in range(await inputs.count()):
            log("  input", i,
                await inputs.nth(i).get_attribute("placeholder"),
                await inputs.nth(i).get_attribute("type"))

        # type XAU in the search box
        try:
            await inputs.first.fill("XAU", timeout=3000)
            await page.wait_for_timeout(2000)
        except Exception as e:
            log("fill error:", e)
        await page.screenshot(path="debug2_2_search.png")

        # dump dropdown text and html
        body = await page.locator("body").inner_text()
        i = body.find("XAU")
        log("TEXT AROUND XAU:", body[max(0, i - 200): i + 800].replace("\n", " | ") if i >= 0 else "NO XAU TEXT")

        for sel in ['[role="option"]', '[role="listbox"]', '[class*="option"]', '[class*="dropdown"]', '[class*="popover"]', '[class*="menu"]']:
            try:
                c = await page.locator(sel).count()
                log(f"{sel}: {c}")
                if c:
                    html = await page.locator(sel).first.evaluate("e => e.outerHTML")
                    log("   HTML:", html[:600].replace("\n", " "))
            except Exception as e:
                log(sel, "error", e)

        # try to click the first item containing XAUUSDT
        try:
            item = page.get_by_text("XAUUSDT").first
            await item.click(timeout=5000)
            await page.wait_for_timeout(6000)
            await page.screenshot(path="debug2_3_after.png", full_page=True)
            body = await page.locator("body").inner_text()
            j = body.lower().find("recent sentiment turns")
            log("TITLE LINE:", [l for l in body.split("\n") if "Liquidation Levels" in l][:3])
            log("AFTER CLICK TURNS:", body[j:j + 250].replace("\n", " | ") if j >= 0 else "NOT FOUND")
        except Exception as e:
            log("click XAUUSDT error:", e)

        Path("debug2_log.txt").write_text("\n".join(out))
        await b.close()

asyncio.run(main())
