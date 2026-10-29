#!/usr/bin/env node
// Retakes the product screenshots on the sales site (web/site/img/) from a fresh demo studio:
// the booking page on a phone in each language, and the staff desk's calendar.
//
//   node tools/site_screenshots.js
'use strict';
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..');
const PY = process.env.PYTHON || 'python3';
const PORT = 8890 + Math.floor(Math.random() * 100);
const OUT = path.join(ROOT, 'web/site/img');

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rota-shots-'));
  const env = { ...process.env, ROTA_DATA: dir, ROTA_PORT: String(PORT), ROTA_PUBLIC_URL: `http://localhost:${PORT}` };
  const r = spawnSync(PY, ['manage.py', 'reset-demo'], { cwd: ROOT, env, encoding: 'utf8' });
  if (r.status) throw new Error(r.stderr);
  const server = spawn(PY, ['server.py'], { cwd: ROOT, env, stdio: 'ignore' });
  const demo = `http://demo.localhost:${PORT}`;
  for (let i = 0; i < 50; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/api/health`)).ok) break; } catch (e) { /* starting */ } await new Promise(r => setTimeout(r, 100)); }
  const browser = await chromium.launch();
  try {
    fs.mkdirSync(OUT, { recursive: true });
    for (const lang of ['hy', 'ru', 'en']) {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, colorScheme: 'light' });
      const p = await ctx.newPage();
      await p.addInitScript(l => localStorage.setItem('rota-lang', JSON.stringify(l)), lang);
      await p.goto(demo + '/'); await p.waitForSelector('.svc'); await p.waitForTimeout(400);
      await p.screenshot({ path: path.join(OUT, `book-${lang}.jpg`), type: 'jpeg', quality: 78 });
      await ctx.close();
    }
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: 'light' });
    const d = await ctx.newPage();
    await d.goto(demo + '/desk/'); await d.click('#demo-go'); await d.waitForSelector('.appt'); await d.waitForTimeout(600);
    await d.evaluate(() => document.querySelector('.banner')?.remove()); // the demo notice isn't part of the product
    await d.screenshot({ path: path.join(OUT, 'desk.jpg'), type: 'jpeg', quality: 78 });
    await ctx.close();
    for (const f of fs.readdirSync(OUT)) console.log(`${f}  ${Math.round(fs.statSync(path.join(OUT, f)).size / 1024)} KB`);
  } finally {
    await browser.close(); server.kill(); fs.rmSync(dir, { recursive: true, force: true });
  }
})().catch(e => { console.error(e); process.exit(1); });
