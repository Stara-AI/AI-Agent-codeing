// Optional real-browser check. Requires Playwright and an available Chromium.
'use strict';
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const [report, output, executablePath] = process.argv.slice(2);
if (!report || !output) throw new Error('usage: node tools/check_report_browser.js REPORT/index.html NEW_OUTPUT_DIR [CHROMIUM_EXE]');
fs.mkdirSync(output, {recursive: false});
(async () => {
  const browser = await chromium.launch({headless: true, ...(executablePath ? {executablePath} : {})});
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    const errors=[];
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(pathToFileURL(path.resolve(report)).href);
    assert.ok(await page.locator('#source-banner').innerText(), 'source must be visible');
    const before=await page.locator('svg').boundingBox();
    const viewBox=await page.locator('svg').getAttribute('viewBox');
    await page.screenshot({path: path.join(output, 'full.png')});
    const choice=await page.evaluate(()=>{
      const hot=nodes.findIndex(n=>n.name==='hot_leaf'&&n.width>0&&n.width<1);
      const narrow=nodes.findIndex(n=>n.width>0&&n.width<1);
      return hot>=0?hot:narrow>=0?narrow:nodes.length-1;
    });
    const selected=page.locator(`g[data-id="${choice}"]`);
    await selected.click();
    const after=await page.locator('svg').boundingBox();
    assert.ok(Math.abs(before.height-after.height)<0.1, 'zoom must keep rendered height');
    assert.equal(await page.locator('svg').getAttribute('viewBox'), viewBox, 'zoom must keep aspect ratio');
    assert.equal(Number(await selected.locator('rect').getAttribute('width')), 1200);
    assert.ok(await page.locator('#frame-detail').innerText());
    await page.screenshot({path: path.join(output, 'zoom.png')});
    await page.locator('#search').fill('HOT_LEAF');
    const expected=await page.evaluate(()=>nodes.filter(n=>n.name.toLowerCase().includes('hot_leaf')).length);
    assert.equal(await page.locator('g.hit').count(), expected, 'case-insensitive search');
    await page.locator('#reset').click();
    assert.equal(await page.locator('g[data-id][style*="none"]').count(), 0, 'reset must reveal all paths');
    await page.locator('#search').fill('');
    assert.equal(await page.locator('g.hit').count(), 0);
    await page.setViewportSize({width: 390, height: 844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1), 'small screen must not overflow page');
    await page.screenshot({path: path.join(output, 'mobile.png')});
    assert.deepEqual(errors, [], 'no browser script errors');
    const result={check: 'real Chromium offline report', browser_version: browser.version(),
      viewport: {width: 1440, height: 1000}, before, after, stable_height: true,
      zoom: true, search: true, reset: true, mobile_no_page_overflow: true,
      page_errors: errors, source_scope: 'UI only; does not validate Linux perf or CPU overhead'};
    fs.writeFileSync(path.join(output, 'result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
  } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
