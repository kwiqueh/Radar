// Optional developer check: requires Node.js and Playwright, not needed to run the app.
const { chromium } = require('playwright');
const { mkdirSync } = require('node:fs');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || '/usr/bin/chromium',
    headless: true, args: ['--no-sandbox'],
  });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1080 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.goto('http://127.0.0.1:8000', { waitUntil: 'networkidle' });
    await page.locator('#offers tr[data-index]').first().waitFor();
    assert.match(await page.locator('#mode').textContent(), /DEMO/);
    await page.locator('#offers tr[data-index]').first().click();
    await page.locator('#offer-detail a').first().waitFor();
    assert.match(await page.locator('#offer-detail').textContent(), /191[,.]12/);
    assert.equal(await page.locator('#offer-detail a').count(), 2);
    await page.locator('th[data-sort="roi"]').click();
    mkdirSync('docs/screenshots', { recursive: true });
    await page.screenshot({ path: 'docs/screenshots/dashboard-demo-desktop.png', fullPage: true });
    await page.locator('[name="min_profit_rub"]').fill('500');
    await page.locator('#save-filters').click();
    await page.waitForFunction(() => document.querySelector('#candidate-count').textContent === '0');
    await page.locator('[name="min_profit_rub"]').fill('150');
    await page.locator('#save-filters').click();
    await page.locator('#offers tr[data-index]').first().waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({ path: 'docs/screenshots/dashboard-demo-mobile.png', fullPage: true });
    assert(await page.evaluate(() => document.body.scrollWidth <= window.innerWidth));
    assert.deepEqual(errors, []);
    console.log('Browser smoke passed: demo labels, details, sorting, filters, mobile; no JS errors.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
