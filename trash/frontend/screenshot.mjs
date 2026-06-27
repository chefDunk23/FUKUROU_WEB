import { chromium } from './node_modules/playwright/index.mjs';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.goto('http://localhost:5173/', { waitUntil: 'networkidle' });
await page.screenshot({ path: 'C:/Temp/home_screen.png' });
await page.goto('http://localhost:5173/dev', { waitUntil: 'networkidle' });
await page.screenshot({ path: 'C:/Temp/dev_screen.png' });
await browser.close();
console.log('done');
