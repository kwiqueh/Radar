// Explicit live check, not run by CI. Uses an isolated server/config (default port 8011).
const { chromium } = require('playwright');
const assert = require('node:assert/strict');
(async () => {
  const browser = await chromium.launch({executablePath:'/usr/bin/chromium',args:['--no-sandbox']});
  try {
    const page = await browser.newPage({viewport:{width:1440,height:1000}});
    const errors=[]; page.on('pageerror', e=>errors.push(e.message));
    await page.goto(process.env.RADAR_TEST_URL || 'http://127.0.0.1:8011',{waitUntil:'networkidle'});
    await page.locator('#setup-mode').selectOption('live');
    await page.locator('[name="source"][value="market_csgo"]').check();
    await page.locator('[name="source"][value="csfloat"]').check();
    await page.locator('[name="source"][value="skinport"]').check();
    await page.locator('#setup-terms').check();
    await Promise.all([
      page.waitForNavigation({waitUntil:'networkidle',timeout:120000}),
      page.locator('#apply-setup').click()
    ]);
    assert.match(await page.locator('#mode').textContent(), /LIVE/);
    await page.locator('#quote-search').fill('AK-47 | Redline');
    await page.locator('#catalog-form button').click();
    await page.waitForFunction(()=>document.querySelector('#quote-rows').textContent.includes('Redline'));
    const result=await page.evaluate(()=>fetch('/api/quotes?provider=market_csgo').then(r=>r.json()));
    assert(result.total>1000);
    assert(result.items.every(q=>!q.demo && q.kind==='catalog_reference' && q.currency==='RUB'));
    const opportunities=await page.evaluate(()=>fetch('/api/opportunities').then(r=>r.json()));
    assert.deepEqual(opportunities,[]); // A reference catalog cannot establish arbitrage.
    const row=page.locator('#quote-rows tr').first();
    await row.scrollIntoViewIfNeeded();
    await page.screenshot({path:'docs/screenshots/live-market-catalog.png'});
    await page.setViewportSize({width:390,height:844});
    await row.scrollIntoViewIfNeeded();
    assert(await page.evaluate(()=>document.body.scrollWidth<=innerWidth));
    assert.deepEqual(errors,[]);
    console.log(`Live browser check passed: ${result.total} real RUB references, search, mode switch, no fake opportunities, mobile layout.`);
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1});
