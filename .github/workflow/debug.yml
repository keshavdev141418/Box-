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
        await page.screenshot(path="debug_1_top.png")

        # scroll to trigger lazy load
        for _ in range(10):
            await page.mouse.wheel(0, 1200)
            await page.wait_for_timeout(800)

        text = await page.locator("body").inner_text()
        low = text.lower()
        log("TITLE:", await page.title())
        log("HAS CLLD:", "clld" in low)
        log("HAS RECENT SENTIMENT TURNS:", "recent sentiment turns" in low)
        log("HAS CLOUDFLARE/VERIFY:", any(k in low for k in ["verify you are human", "cloudflare", "just a moment", "captcha"]))
        i = low.find("recent sentiment turns")
        log("SNIPPET:", text[i:i + 300].replace("\n", " | ") if i >= 0 else "NOT FOUND")
        log("BODY START:", text[:400].replace("\n", " | "))

        await page.screenshot(path="debug_2_full.png", full_page=True)
        Path("debug_page.txt").write_text(text[:30000])

        # dropdowns
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(1000)
        combos = page.locator('[role="combobox"]')
        n = await combos.count()
        log("COMBOBOX COUNT:", n)

        for i in range(n):
            try:
                label = (await combos.nth(i).inner_text(timeout=3000))[:60].replace("\n", " ")
                log(f"combobox {i} text:", label)
                await combos.nth(i).click(timeout=5000)
                await page.wait_for_timeout(1500)
                opts = page.locator('[role="option"]')
                oc = await opts.count()
                log(f"  options after click: {oc}")
                for k in range(min(oc, 40)):
                    log("   opt", k, (await opts.nth(k).inner_text())[:50].replace("\n", " "))
                inputs = await page.locator("input:visible").count()
                log("  visible inputs:", inputs)
                await page.screenshot(path=f"debug_3_combo{i}.png")
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(500)
            except Exception as e:
                log(f"combobox {i} error:", e)

        Path("debug_log.txt").write_text("\n".join(out))
        await b.close()

asyncio.run(main())
