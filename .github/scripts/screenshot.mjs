// Opens the live demo (served locally from docs/), waits for the USGS feed and coastlines,
// and saves a screenshot of the console to screenshots/live-demo.png.
import { chromium } from 'playwright';

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.5 });
await page.addInitScript(() => {
  try { localStorage.setItem('gqw2', JSON.stringify({ win: 10080, minMag: 2.5, view: 'flat' })); } catch (e) {}
});
await page.goto('http://localhost:8000/', { waitUntil: 'networkidle' });
await page.waitForSelector('.status:not(.offline) #liveLbl', { timeout: 30000 });
await page.waitForFunction(() => document.getElementById('liveLbl').textContent.startsWith('Live'), null, { timeout: 30000 });
await page.waitForTimeout(6000); // coastlines + first animation frames
const box = await page.locator('.shell').boundingBox();
await page.screenshot({ path: 'screenshots/live-demo.png', clip: { x: box.x, y: box.y, width: box.width, height: 860 } });
await browser.close();
console.log('saved screenshots/live-demo.png');
