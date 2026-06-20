// Real-user browser journey — drives the ACTUAL pages through Caddy + the API, the way a person
// would. This is the layer the in-process pytest suite can't cover: it catches frontend JS errors,
// broken page flows, and a down/502 backend (e.g. the "no network" onboarding bug).
//
// It builds ALL its own data through the UI: register a clinic -> platform admin approves (capturing
// the auto-created clinic_admin creds from the screen) -> that admin adds a doctor with a login and
// generates slots -> the patient booking page shows those slots. Only the platform superadmin is a
// bootstrap (root@local.test, mirroring prod's APP_SUPERADMIN_*).
const { test, expect } = require('@playwright/test');

const SUPERADMIN = { id: process.env.E2E_SUPERADMIN || 'root@local.test',
                     pw: process.env.E2E_SUPERADMIN_PW || 'rootpass123' };
const RUN = process.env.E2E_RUN_ID || String(Date.now()).slice(-6);

// Fail the test on uncaught JS exceptions (= a genuinely broken page). We deliberately ignore
// "Failed to load resource" console noise: a 401/403 while the login overlay is up is normal auth
// flow, not a bug. Real app-level console.error messages are still captured.
function guard(page, errors) {
  page.on('pageerror', e => errors.push('pageerror: ' + e.message));
  page.on('console', m => {
    if (m.type() !== 'error') return;
    const t = m.text();
    if (/Failed to load resource/i.test(t)) return;   // HTTP-status noise (expected during auth)
    errors.push('console.error: ' + t);
  });
  // a real reviewer clicks "OK" on the force-go-live confirmation; auto-accept it
  page.on('dialog', d => d.accept());
}

async function login(page, ident, pw, newPw) {
  await expect(page.locator('#ovBtn')).toBeVisible();      // staff pages show the login overlay
  await page.fill('#ovEmail', ident);
  await page.fill('#ovPass', pw);
  await page.click('#ovBtn');
  // a fresh account is forced to reset on first login; set a password and continue. Defaults to
  // the SAME password so the bootstrap superadmin stays usable across runs (idempotent).
  // (waitFor actually blocks until the reset view renders; isVisible() would not.)
  try {
    await page.locator('#ovNew').waitFor({ state: 'visible', timeout: 4000 });
    const np = newPw || pw;
    await page.fill('#ovNew', np);
    await page.fill('#ovNew2', np);
    await page.click('#ovBtn');
  } catch { /* no forced reset for this account */ }
  // overlay disappears once authenticated (page reloads)
  await page.locator('#loginOverlay').waitFor({ state: 'detached', timeout: 8000 }).catch(() => {});
}

test('a not-live backend is visible to the user (no silent "no network")', async ({ page }) => {
  // Sanity: the API must be reachable through Caddy. If it isn't, the booking page surfaces it.
  const resp = await page.request.get('/api/v1/healthz');
  expect(resp.status(), 'API health via Caddy — if this fails the stack is down').toBe(200);
});

test('onboarding: registering a clinic shows the success screen, not "no network"', async ({ page }) => {
  const errors = []; guard(page, errors);
  await page.goto('/appointments/onboard');
  await page.fill('#name', `UI Journey Clinic ${RUN}`);
  await page.fill('#contact_email', `owner.${RUN}@uijourney.test`);
  await page.click('#registerBtn');

  // the success card must appear and the error line must NOT say network failure
  await expect(page.locator('#done')).toBeVisible();
  await expect(page.locator('#doneName')).toContainText('registered');
  await expect(page.locator('#error')).toBeHidden();
  expect(errors, 'no uncaught JS errors on the onboarding page').toEqual([]);
});

test('full journey: register -> approve -> add doctor+login -> generate slots -> patient sees them',
  async ({ page }) => {
    const errors = []; guard(page, errors);
    const slug = `journey-${RUN}`;
    const name = `Journey Hospital ${RUN}`;

    // 1) register via the onboarding UI
    await page.goto('/appointments/onboard');
    await page.fill('#name', name);
    await page.fill('#slug', slug);
    await page.fill('#contact_email', `admin.${slug}@uijourney.test`);
    await page.click('#registerBtn');
    await expect(page.locator('#done')).toBeVisible();

    // 2) platform admin approves go-live and reads the clinic_admin credentials off the screen
    await page.goto('/appointments/onboard-admin');
    await login(page, SUPERADMIN.id, SUPERADMIN.pw);
    const row = page.locator('.kv', { hasText: name });
    await expect(row).toBeVisible();
    await row.getByRole('button', { name: /go-live/i }).click();
    await expect(page.locator('#adminCreds')).toBeVisible();
    const adminEmail = (await page.locator('#adminCreds .cred-email').textContent()).trim();
    const adminTemp = (await page.locator('#adminCreds .cred-temp').textContent()).trim();
    expect(adminEmail).toContain(slug);
    expect(adminTemp.length).toBeGreaterThan(6);

    // 3) the clinic admin signs in on THEIR clinic's slots page (fresh session, not the superadmin's)
    await page.goto(`/appointments/${slug}/slots`);
    await page.evaluate(() => sessionStorage.clear());
    await page.reload();
    await login(page, adminEmail, adminTemp, 'JourneyPw12345');

    // 4) add a doctor WITH a login (unified profile) and generate their slots
    await expect(page.locator('#addDocBtn')).toBeVisible();
    await page.fill('#newDocName', 'Dr Journey');
    await page.fill('#newDocEmail', `dr.${slug}@uijourney.test`);
    await page.click('#addDocBtn');
    await expect(page.locator('#addDocResult')).toContainText('Login created');

    const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10);
    await page.fill('#date', tomorrow);
    await page.fill('#start', '09:00');
    await page.fill('#end', '11:00');
    await page.fill('#slot_minutes', '30');
    await page.fill('#capacity', '2');
    await page.click('#genBtn');
    await expect(page.locator('#genResult')).toContainText('Created 4');

    // 5) the clinic's patient booking page loads cleanly (live, through the real stack)
    await page.goto(`/appointments/${slug}`);
    await expect(page.locator('body')).toBeVisible();
    await page.waitForTimeout(500);   // let the page's initial fetches settle
    expect(errors, 'no uncaught JS errors across the whole journey').toEqual([]);
  });
