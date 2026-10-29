// End-to-end smoke test in a real browser. Starts its own server on a temporary data folder with the
// demo and one sample studio (nairi), then signs a new company up on the sales site, books with it,
// and runs the booking site (Armenian and Russian) and the staff desk.
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
const SITE = `http://localhost:${PORT}`;
const at = slug => `http://${slug}.localhost:${PORT}`;
const OWNER = ['owner@e2e.test', 'e2e-owner-password'];
const ADMIN = ['admin@e2e.test', 'e2e-admin-password'];

let failures = 0;
const check = (name, ok, detail = '') => { console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${!ok && detail ? `\n      ${detail}` : ''}`); if (!ok) failures++; };
// A day at least two days out, so the visit can still be changed online (studios stop online changes a
// few hours before a visit, and the first free time is often later today).
const laterDay = async p => {
  await p.waitForSelector('.slot');
  const open = await p.$$eval('button.day:not([disabled])', els => els.map(e => e.id));
  await p.click('#' + open[Math.min(2, open.length - 1)]); await p.waitForSelector('.slot');
};
const soon = (p, name) => p.then(() => check(name, true), e => check(name, false, e.message.split('\n')[0]));

(async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rota-e2e-'));
  const env = { ...process.env, ROTA_DATA: dir, ROTA_PORT: String(PORT), ROTA_PUBLIC_URL: SITE, ROTA_BOOKINGS_PER_HOUR: '50' };
  const run = (args, input) => { const r = spawnSync(PY, args, { cwd: ROOT, env, input, encoding: 'utf8' }); if (r.status) throw new Error(r.stderr || r.stdout); };
  run(['manage.py', 'reset-demo']);
  run(['manage.py', 'create-studio', 'nairi', '--business', 'Atelier Nairi', '--owner', OWNER[0], '--kind', 'hair', '--sample', '--password-stdin'], OWNER[1] + '\n');
  run(['manage.py', 'create-admin', ADMIN[0], '--password-stdin'], ADMIN[1] + '\n');
  const server = spawn(PY, ['server.py'], { cwd: ROOT, env, stdio: ['ignore', 'ignore', 'pipe'] });
  let serverLog = ''; server.stderr.on('data', d => { serverLog += d; });
  for (let i = 0; i < 50; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/api/health`)).ok) break; } catch (e) { /* starting */ } await new Promise(r => setTimeout(r, 100)); }

  // Studios live at <name>.localhost; make sure every Chromium resolves those to this machine.
  const browser = await chromium.launch({ args: ['--host-resolver-rules=MAP *.localhost 127.0.0.1'] });
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
    // ---- a company signs up on the sales site
    const s = await page({ locale: 'hy-AM' });
    await s.goto(SITE + '/'); await s.waitForSelector('#hero-start');
    check('the sales site opens in Armenian', (await s.evaluate(() => document.documentElement.lang)) === 'hy');
    await s.click('[data-lang="ru"]');
    check('the sales site switches to Russian', /Клиенты/.test(await s.textContent('h1')));
    await s.click('#hero-start'); await s.waitForSelector('#signup');
    await s.fill('#su-business', 'Ատելիե Լուսինե');
    await soon(s.locator('#slug-status.ok').waitFor({ timeout: 5000 }), 'the address is suggested from an Armenian name and checked');
    check('the suggestion is in Latin letters', (await s.inputValue('#su-slug')) === 'atelie-lusine', await s.inputValue('#su-slug'));
    await s.click('#su-go');
    check('missing fields are explained next to them', (await s.getAttribute('#su-name', 'aria-invalid')) === 'true' && !!(await s.textContent('#err-name')));
    await s.click('label:has(input[value="nails"])');
    await s.fill('#su-name', 'Լուսինե'); await s.fill('#su-phone', '+374 91 000 303'); await s.fill('#su-email', 'lusine@e2e.test'); await s.fill('#su-password', 'lusine-password-1');
    await s.selectOption('#su-lang', 'hy');
    await s.click('#su-go');
    await s.waitForURL(/atelie-lusine\.localhost.*\/desk\//); await s.waitForSelector('.steps');
    check('sign-up lands the owner, signed in, on the getting-started page', /Welcome/.test(await s.textContent('h1')));
    check('the one-time link is gone from the address bar', !/welcome=/.test(s.url()), s.url());

    // ---- a client books with the brand-new studio, and its desk is told
    const nc = await page({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, locale: 'hy-AM' });
    await nc.goto(at('atelie-lusine') + '/'); await nc.waitForSelector('.svc');
    check('the new studio has starter services for nails', /Մատնահարդարում/.test(await nc.textContent('#main')));
    await nc.locator('label.svc').first().click(); await nc.click('#bk-next');
    await nc.waitForSelector('.pro'); await nc.click('.pro');
    await nc.waitForSelector('.slot'); await nc.click('.slot');
    await nc.fill('#bk-name', 'Առաջին Հաճախորդ'); await nc.fill('#bk-phone', '+374 91 000 404'); await nc.click('#bk-confirm');
    await nc.waitForSelector('.done-card');
    await soon(s.locator('#toasts', { hasText: 'New online booking' }).waitFor({ timeout: 10000 }), "the new studio's desk hears about its first online booking");

    // ---- book at the sample studio in Armenian
    const c = await page();
    await c.addInitScript(() => localStorage.setItem('rota-lang', '"hy"'));
    await c.goto(at('nairi') + '/'); await c.waitForSelector('.svc');
    check('booking site opens in Armenian', (await c.evaluate(() => document.documentElement.lang)) === 'hy');
    await c.click('label[for="sv-sv-gel"]'); await c.click('#bk-next');
    await c.waitForSelector('.pro'); await c.click('.pro');
    await laterDay(c); await c.click('.slot');
    await c.waitForSelector('#bk-name'); await c.click('#bk-confirm');
    check('missing name and phone are explained next to the fields', !!(await c.$('#bk-name[aria-invalid="true"]')) && !!(await c.$('#bk-phone[aria-invalid="true"]')));
    await c.fill('#bk-name', 'Անի Թեստ'); await c.fill('#bk-phone', '+374 91 000 101'); await c.click('#bk-confirm');
    await c.waitForSelector('.done-card');
    const link = await c.$eval('#manage-url', e => e.textContent);
    check('a booking returns a private manage link', new RegExp(`^${at('nairi').replace(/\./g, '\\.')}/b/a_[0-9a-f]+\\.[\\w-]+$`).test(link), link);
    // ---- manage from the link on another device
    const other = await page({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
    await other.addInitScript(() => localStorage.setItem('rota-lang', '"ru"'));
    await other.goto(link); await other.waitForSelector('.bk.hl');
    check('the manage link opens the booking on a new device', !!(await other.$('.bk.hl [data-act="resched"]')));
    await other.click('.bk.hl [data-act="resched"]'); await laterDay(other);
    const slots = await other.$$('.slot'); await slots[slots.length - 1].click();
    await other.waitForSelector('#bk-confirm'); await other.click('#bk-confirm'); await other.waitForSelector('.done-card');
    check('the client can move the visit', /перенес/i.test(await other.$eval('.done-card h2', e => e.textContent)));
    check('no sideways scrolling on a phone', (await other.evaluate(() => document.documentElement.scrollWidth - innerWidth)) <= 0);
    // ---- the desk sees it; typing survives the sign-in page filling in the studio's name late
    const d = await page({ viewport: { width: 1440, height: 900 } });
    await d.route('**/api/public/studio', async r => { await new Promise(x => setTimeout(x, 1200)); await r.continue(); });
    await d.goto(at('nairi') + '/desk/'); await d.waitForSelector('#li-email');
    await d.fill('#li-email', OWNER[0]); await d.fill('#li-pw', 'nope'); await d.waitForTimeout(1500); await d.click('#li-go');
    await d.locator('#li-err', { hasText: 'match' }).waitFor();
    check('a wrong password keeps the email filled in', (await d.$eval('#li-email', e => e.value)) === OWNER[0]);
    await d.fill('#li-pw', OWNER[1]); await d.click('#li-go'); await d.waitForSelector('.cal-col');
    await d.click('#nav-reminders'); await d.click('#rr-week'); await d.waitForSelector('.rem-row');
    const wa = await d.$$eval('.rem-row a.btn.wa', as => as.map(a => decodeURIComponent(a.href)));
    check("reminders carry the client's manage link", wa.some(h => h.includes('nairi.localhost') && h.includes('/b/a')), wa[0]);
    // ---- client cancels, desk is told
    await other.click('#bk-mine'); await other.waitForSelector('[data-act="cancel"]');
    await other.click('[data-act="cancel"]'); await other.click('#cancel-yes');
    // Wait with locators, not waitForFunction: older Playwright evaluates its predicate with eval(),
    // which the desk's Content-Security-Policy rightly refuses.
    await soon(d.locator('#toasts', { hasText: 'cancelled their' }).waitFor({ timeout: 10000 }), 'the desk hears about an online cancellation');
    await d.click('#logout'); await d.waitForSelector('#li-email');
    check('signing out returns to the sign-in form', true);
    // ---- the demo and Rota's admin page
    const demo = await page();
    await demo.goto(at('demo') + '/desk/'); await demo.click('#demo-go');
    await soon(demo.locator('.appt').first().waitFor({ timeout: 10000 }), 'the demo desk opens without a password');
    const a = await page();
    await a.goto(SITE + '/admin/'); await a.fill('#ad-email', ADMIN[0]); await a.fill('#ad-pw', ADMIN[1]); await a.click('#signin button');
    await soon(a.locator('.studio', { hasText: 'atelie-lusine' }).waitFor({ timeout: 5000 }), "the new company shows on Rota's admin page");
  } catch (e) {
    check('run completed', false, e.stack);
  } finally {
    check('no script or console errors', errors.length === 0, errors.slice(0, 3).join(' | '));
    await browser.close();
    server.kill();
    fs.rmSync(dir, { recursive: true, force: true });
    if (failures && serverLog) console.log('\nserver log:\n' + serverLog.slice(-2500));
    console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
    process.exit(failures ? 1 : 0);
  }
})();
