// End-to-end smoke test in a real browser. Starts its own server on a temporary database with the
// sample studio, then drives the booking site (Armenian and Russian) and the staff desk.
//
//   npm install --no-save playwright && npx playwright install chromium
//   node tests/e2e/smoke.js
const { spawn, spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');

const ROOT = path.resolve(__dirname, '..', '..');
const PY = process.env.PYTHON || 'python3';
const PORT = 8790 + Math.floor(Math.random() * 100);
const BASE = `http://127.0.0.1:${PORT}`;
const OWNER = ['owner@e2e.test', 'e2e-owner-password'];

let failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? `\n      ${detail}` : ''}`); if (!ok) failures++; };

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rota-e2e-'));
  const env = { ...process.env, ROTA_DB: path.join(dir, 'e2e.db'), ROTA_PORT: String(PORT), ROTA_BOOKINGS_PER_HOUR: '50' };
  const run = (args, input) => { const r = spawnSync(PY, args, { cwd: ROOT, env, input, encoding: 'utf8' }); if (r.status) throw new Error(r.stderr || r.stdout); };
  run(['manage.py', 'seed-sample']);
  run(['manage.py', 'create-user', OWNER[0], '--role', 'owner', '--password-stdin'], OWNER[1] + '\n');
  const server = spawn(PY, ['server.py'], { cwd: ROOT, env, stdio: ['ignore', 'ignore', 'pipe'] });
  let serverLog = ''; server.stderr.on('data', d => { serverLog += d; });
  for (let i = 0; i < 50; i++) { try { if ((await fetch(BASE + '/api/health')).ok) break; } catch (e) { /* starting */ } await new Promise(r => setTimeout(r, 100)); }

  const browser = await chromium.launch();
  const errors = [];
  const page = async (opts = {}) => {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, ...opts });
    await ctx.route(/fonts\.(googleapis|gstatic)\.com|wa\.me|google\.com/, r => r.abort());
    const p = await ctx.newPage();
    p.on('pageerror', e => errors.push(e.message));
    p.on('console', m => { if (m.type() === 'error' && !/ERR_|net::|401/.test(m.text())) errors.push(m.text()); });
    return p;
  };
  try {
    // ---- book in Armenian
    const c = await page();
    await c.addInitScript(() => localStorage.setItem('rota-lang', '"hy"'));
    await c.goto(BASE + '/'); await c.waitForSelector('.svc');
    check('booking site opens in Armenian', (await c.evaluate(() => document.documentElement.lang)) === 'hy');
    await c.click('label[for="sv-sv-gel"]'); await c.click('#bk-next');
    await c.waitForSelector('.pro'); await c.click('.pro');
    await c.waitForSelector('.slot'); await c.click('.slot');
    await c.waitForSelector('#bk-name'); await c.click('#bk-confirm');
    check('missing name and phone are explained next to the fields', !!(await c.$('#bk-name[aria-invalid="true"]')) && !!(await c.$('#bk-phone[aria-invalid="true"]')));
    await c.fill('#bk-name', 'Անի Թեստ'); await c.fill('#bk-phone', '+374 91 000 101'); await c.click('#bk-confirm');
    await c.waitForSelector('.done-card');
    const link = await c.$eval('#manage-url', e => e.textContent);
    check('a booking returns a private manage link', /\/b\/a_[0-9a-f]+\.[\w-]+$/.test(link), link);
    // ---- manage from the link on another device
    const other = await page({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await other.addInitScript(() => localStorage.setItem('rota-lang', '"ru"'));
    await other.goto(link); await other.waitForSelector('.bk.hl');
    check('the manage link opens the booking on a new device', !!(await other.$('.bk.hl [data-act="resched"]')));
    await other.click('.bk.hl [data-act="resched"]'); await other.waitForSelector('.slot');
    const slots = await other.$$('.slot'); await slots[slots.length - 1].click();
    await other.waitForSelector('#bk-confirm'); await other.click('#bk-confirm'); await other.waitForSelector('.done-card');
    check('the client can move the visit', /перенес/i.test(await other.$eval('.done-card h2', e => e.textContent)));
    check('no sideways scrolling on a phone', (await other.evaluate(() => document.documentElement.scrollWidth - innerWidth)) <= 0);
    // ---- the desk sees it
    const d = await page({ viewport: { width: 1440, height: 900 } });
    await d.goto(BASE + '/desk/'); await d.waitForSelector('#li-email');
    await d.fill('#li-email', OWNER[0]); await d.fill('#li-pw', 'nope'); await d.click('#li-go');
    // Wait with locators, not waitForFunction: older Playwright evaluates its predicate with eval(),
    // which the desk's Content-Security-Policy rightly refuses.
    await d.locator('#li-err', { hasText: 'match' }).waitFor();
    check('a wrong password keeps the email filled in', (await d.$eval('#li-email', e => e.value)) === OWNER[0]);
    await d.fill('#li-pw', OWNER[1]); await d.click('#li-go'); await d.waitForSelector('.cal-col');
    await d.click('#nav-reminders'); await d.click('#rr-week'); await d.waitForSelector('.rem-row');
    const wa = await d.$$eval('.rem-row a.btn.wa', as => as.map(a => decodeURIComponent(a.href)));
    check('reminders carry the client\'s manage link', wa.some(h => h.includes('/b/a')), wa[0]);
    // ---- client cancels, desk is told
    await other.click('#bk-mine'); await other.waitForSelector('[data-act="cancel"]');
    await other.click('[data-act="cancel"]'); await other.click('#cancel-yes');
    await d.locator('#toasts', { hasText: 'cancelled their' }).waitFor({ timeout: 10000 }).then(() => check('the desk hears about an online cancellation', true), () => check('the desk hears about an online cancellation', false));
    await d.click('#logout'); await d.waitForSelector('#li-email');
    check('signing out returns to the sign-in form', true);
  } catch (e) {
    check('run completed', false, e.stack);
  } finally {
    check('no script or console errors', errors.length === 0, errors.slice(0, 3).join(' | '));
    await browser.close();
    server.kill();
    fs.rmSync(dir, { recursive: true, force: true });
    if (failures && serverLog) console.log('\nserver log:\n' + serverLog.slice(-2000));
    console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
    process.exit(failures ? 1 : 0);
  }
})();
