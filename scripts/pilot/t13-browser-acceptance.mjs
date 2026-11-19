// T13 browser acceptance against a running fictional pilot runtime (real API + SQLite).
// Setup (patients, visit, T04 execution) uses the existing T01/T02/T04 HTTP API; every T13 money action
// is done in the UI: reception collects for a visit with two completed procedures and one cancelled,
// records an advance on another patient, the manager adjusts and reverses a payment with a reason,
// the doctor reads only, reload persistence, another clinic sees nothing, daily report per method.
// The browser runs in UTC while the clinic is Europe/Bucharest. Credentials never reach the summary.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const required = ['DENTACARE_PILOT_HOME', 'DENTACARE_APP_SHA', 'PILOT_WEB_URL', 'PILOT_API_URL', 'PILOT_CREDENTIALS', 'PLAYWRIGHT_MODULE', 'CHROMIUM_EXECUTABLE'];
for (const key of required) if (!process.env[key]) throw new Error(`Missing ${key}`);
const { chromium, request } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const creds = JSON.parse(await fs.readFile(process.env.PILOT_CREDENTIALS, 'utf8'));
const web = process.env.PILOT_WEB_URL.replace(/\/$/, '');
const api = process.env.PILOT_API_URL.replace(/\/$/, '');
const evidenceDir = process.env.T13_EVIDENCE_DIR || path.join(process.env.DENTACARE_PILOT_HOME, 'browser-evidence', 't13');
await fs.mkdir(evidenceDir, { recursive: true, mode: 0o700 });
const runId = new Date().toISOString().replaceAll(':', '-');
const tag = runId.slice(11, 19).replaceAll('-', '');
const BROWSER_TZ = 'UTC';
const summary = { task: 'T13', appSha: process.env.DENTACARE_APP_SHA, web, api, browser: null, browserTimezone: BROWSER_TZ, clinicTimezone: null,
  checks: [], artifacts: [], pageErrors: [], consoleErrors: [], network: { entryPosts: 0, reversePosts: 0 }, errors: [] };
const check = (name, ok, details = {}) => { summary.checks.push({ name, passed: Boolean(ok), ...details }); if (!ok) throw new Error(`Acceptance failed: ${name} ${JSON.stringify(details)}`); };
const shot = async (page, name) => { const file = path.join(evidenceDir, `t13-${name}.png`); await page.screenshot({ path: file, fullPage: true }); summary.artifacts.push(path.basename(file)); };
const idPath = url => { try { return new URL(url).pathname.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id'); } catch { return null; } };
const cents = value => Math.round(Number(value) * 100);
const money = value => (value / 100).toFixed(2);

async function apiSession(account, label) {
  const ctx = await request.newContext();
  const login = await ctx.post(`${api}/auth/login`, { data: { email: account.email, password: account.password } });
  if (!login.ok()) throw new Error(`API login failed for ${label}: ${login.status()}`);
  return session(ctx, (await login.json()).access_token);
}
function session(ctx, token) {
  const call = async (method, url, data) => {
    const response = await ctx.fetch(`${api}${url}`, { method, data, headers: { Authorization: `Bearer ${token}` } });
    return { status: response.status(), body: await response.json().catch(() => null) };
  };
  return { ctx, call };
}

const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
summary.browser = browser.version();
const contexts = [];
async function login(account, label) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 950 }, timezoneId: BROWSER_TZ, locale: 'ro-RO' });
  contexts.push(context);
  const page = await context.newPage();
  page.on('pageerror', error => summary.pageErrors.push({ user: label, message: String(error.message).slice(0, 300) }));
  page.on('console', msg => { if (msg.type() === 'error') summary.consoleErrors.push({ user: label, text: msg.text().slice(0, 200), sourcePath: idPath(msg.location()?.url ?? '') }); });
  page.on('response', response => { if (response.status() >= 400) summary.consoleErrors.push({ user: label, httpStatus: response.status(), method: response.request().method(), path: idPath(response.url()) }); });
  page.on('request', req => {
    const p = new URL(req.url()).pathname;
    if (req.method() === 'POST' && /\/api\/finance\/patients\/[^/]+\/entries$/.test(p)) summary.network.entryPosts += 1;
    if (req.method() === 'POST' && /\/api\/finance\/entries\/[^/]+\/reverse$/.test(p)) summary.network.reversePosts += 1;
  });
  await page.goto(web, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill(account.email);
  await page.getByLabel('Password').fill(account.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Patients', exact: true }).waitFor();
  return { context, page };
}
const panelOf = page => page.getByRole('region', { name: 'Cont pacient' });
async function openAccount(page, patient) {
  await page.goto(`${web}/patients/${patient.id}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { level: 1, name: `${patient.first_name} ${patient.last_name}` }).waitFor();
  await panelOf(page).getByTestId('account-balance').waitFor();
  return panelOf(page);
}
const totals = async panel => ({
  services: (await panel.getByTestId('account-services').innerText()).trim(),
  paid: (await panel.getByTestId('account-paid').innerText()).trim(),
  balance: (await panel.getByTestId('account-balance').innerText()).trim(),
});
async function record(page, panel, { kind, amount, method, reason, visitId }) {
  await panel.getByLabel('Tip mișcare').selectOption(kind);
  await panel.getByLabel('Sumă', { exact: true }).fill(amount);
  if (method) await panel.getByLabel('Metodă', { exact: true }).selectOption(method);
  await panel.getByLabel('Vizită asociată').selectOption(visitId ?? '');
  if (reason !== undefined) await panel.getByLabel('Motiv', { exact: true }).fill(reason);
  const before = summary.network.entryPosts;
  const done = page.waitForResponse(r => r.request().method() === 'POST' && /\/api\/finance\/patients\/[^/]+\/entries$/.test(new URL(r.url()).pathname));
  const refetched = page.waitForResponse(r => r.request().method() === 'GET' && /\/api\/finance\/patients\/[^/]+\/account$/.test(new URL(r.url()).pathname));
  await panel.getByRole('button', { name: 'Înregistrează' }).dblclick();
  const response = await done;
  await refetched;
  await panel.getByTestId('finance-form-saved').waitFor();
  return { status: response.status(), posts: summary.network.entryPosts - before };
}

const doctorApi = await apiSession(creds.doctor, 'doctor');
const receptionApi = await apiSession(creds.reception, 'reception');
let activePage = null;
try {
  // ---- Setup through existing APIs (fictional data) ----
  const clinic = (await doctorApi.call('GET', '/clinic')).body;
  summary.clinicTimezone = clinic.timezone;
  check('clinic zone differs from browser zone', clinic.timezone && clinic.timezone !== BROWSER_TZ, { clinicTimezone: clinic.timezone });
  const tz = clinic.timezone;
  const me = (await doctorApi.call('GET', '/auth/me')).body;
  const rooms = (await receptionApi.call('GET', '/rooms')).body;
  const catalog = (await doctorApi.call('GET', '/procedures?active=true&limit=50')).body;
  check('three active catalog services', catalog.length >= 3, { services: catalog.length });
  const mkPatient = async first => (await receptionApi.call('POST', '/patients', { first_name: first, last_name: `T13Fictiv${tag}` })).body;
  const patientA = await mkPatient('Doina');
  const patientB = await mkPatient('Emil');
  let visit = null;
  for (let attempt = 0; attempt < 25 && (!visit || visit.status === 409); attempt++) {
    const start = new Date(Date.now() + (2 + Math.floor(Math.random() * 150)) * 86400000);
    if ([0, 6].includes(start.getUTCDay())) continue;
    start.setUTCHours(7 + Math.floor(Math.random() * 6), Math.floor(Math.random() * 6) * 10, 0, 0);
    visit = await receptionApi.call('POST', '/appointments', { patient_id: patientA.id, doctor_id: me.id, room_id: rooms[attempt % rooms.length].id, starts_at: start.toISOString(), duration_minutes: 60, appointment_type: 'Tratament T13 fictiv' });
  }
  check('reception books the visit', visit?.status === 201, { status: visit?.status });
  const visitId = visit.body.id;
  const runProcedure = async (service, final) => {
    const created = await doctorApi.call('POST', '/performed-procedures', { appointment_id: visitId, procedure_catalog_id: service.id });
    const id = created.body.id;
    if (final === 'completed') {
      await doctorApi.call('POST', `/performed-procedures/${id}/start`);
      const done = await doctorApi.call('POST', `/performed-procedures/${id}/complete`);
      return done.status === 200 ? done.body : null;
    }
    const cancelled = await doctorApi.call('POST', `/performed-procedures/${id}/cancel`, { transition: 'cancel', notes: 'Anulat fictiv' });
    return cancelled.status === 200 ? cancelled.body : null;
  };
  const p1 = await runProcedure(catalog[0], 'completed');
  const p2 = await runProcedure(catalog[1], 'completed');
  const p3 = await runProcedure(catalog[2], 'cancelled');
  check('T04: two procedures completed and one cancelled', p1?.status === 'completed' && p2?.status === 'completed' && p3?.status === 'cancelled');
  const owed = cents(p1.current_price) + cents(p2.current_price);
  summary.ids = { patientA: patientA.id, patientB: patientB.id, visit: visitId };

  // ---- Reception: account panel, visit-linked collection, extra card payment ----
  const reception = await login(creds.reception, 'reception'); activePage = reception.page;
  let panel = await openAccount(reception.page, patientA);
  let t = await totals(panel);
  check('services total = two completed (cancelled excluded), nothing paid yet', t.services === `${money(owed)} RON` && t.paid === '0.00 RON' && t.balance === `${money(owed)} RON de plată`, t);
  check('cancelled procedure shown without amount', (await panel.locator('[data-procedure-status="cancelled"]').innerText()).includes('anulată, fără sumă'));
  await reception.page.goto(`${web}/appointments/${visitId}/visit`, { waitUntil: 'domcontentloaded' });
  const visitCard = reception.page.getByRole('region', { name: 'Încasare vizită' });
  await visitCard.getByTestId('visit-services').waitFor();
  check('visit page shows the visit services total', (await visitCard.getByTestId('visit-services').innerText()) === `${money(owed)} RON`);
  const placeholder = await visitCard.getByLabel('Sumă vizită').getAttribute('placeholder');
  check('visit quick payment proposes the remaining amount', placeholder === money(owed), { placeholder });
  await visitCard.getByLabel('Metodă vizită').selectOption('cash');
  const beforeVisitPay = summary.network.entryPosts;
  const visitPay = reception.page.waitForResponse(r => r.request().method() === 'POST' && /\/api\/finance\/patients\/[^/]+\/entries$/.test(new URL(r.url()).pathname));
  await visitCard.getByRole('button', { name: 'Încasează pentru vizită' }).dblclick();
  const visitPayResponse = await visitPay;
  await visitCard.getByTestId('visit-paid').filter({ hasText: `${money(owed)} RON` }).waitFor();
  check('visit-linked cash payment saved once (double click guarded)', visitPayResponse.status() === 201 && summary.network.entryPosts - beforeVisitPay === 1, { posts: summary.network.entryPosts - beforeVisitPay });
  await shot(reception.page, 'visit-paid');
  panel = await openAccount(reception.page, patientA);
  t = await totals(panel);
  check('balance settled after the visit payment', t.paid === `${money(owed)} RON` && t.balance === '0.00 RON · achitat', t);
  const kinds = await panel.getByLabel('Tip mișcare').locator('option').evaluateAll(o => o.map(x => x.value));
  check('reception is offered payment/refund only, no adjustments', JSON.stringify(kinds) === JSON.stringify(['payment', 'refund']), { kinds });
  await panel.getByLabel('Sumă', { exact: true }).fill('1.234');
  await panel.getByRole('button', { name: 'Înregistrează' }).click();
  check('invalid amount refused in UI without a request', (await panel.getByTestId('finance-form-error').innerText()).includes('două zecimale'));
  const extra = await record(reception.page, panel, { kind: 'payment', amount: '50.00', method: 'card' });
  check('extra card payment saved once (double click)', extra.status === 201 && extra.posts === 1, extra);
  t = await totals(panel);
  check('overpayment shown as explicit patient credit', t.balance === '50.00 RON credit (avans al pacientului)', t);
  const receptionName = (await receptionApi.call('GET', '/auth/me')).body.full_name;
  const apiCard = (await receptionApi.call('GET', `/finance/patients/${patientA.id}/account`)).body.entries.find(e => e.method === 'card');
  const fmt = zone => new Intl.DateTimeFormat('ro-RO', { dateStyle: 'medium', timeStyle: 'short', timeZone: zone }).format(new Date(apiCard.created_at));
  const cardText = await panel.locator(`[data-entry-id="${apiCard.id}"]`).innerText();
  check('movement shows author and time in the clinic zone (API offset explicit)', /(\+00:00|Z)$/.test(apiCard.created_at)
    && cardText.includes(`${receptionName} · ${fmt(tz)}`) && fmt(tz) !== fmt(BROWSER_TZ), { createdAtHasOffset: /(\+00:00|Z)$/.test(apiCard.created_at) });
  const receptionAdjust = await receptionApi.call('POST', `/finance/patients/${patientA.id}/entries`, { kind: 'adjustment_debit', amount: '5.00', reason: 'x', client_request_id: crypto.randomUUID() });
  check('reception adjustment refused by API (403)', receptionAdjust.status === 403, { status: receptionAdjust.status });

  // ---- Reception: advance on another patient ----
  const panelB = await openAccount(reception.page, patientB);
  const advance = await record(reception.page, panelB, { kind: 'payment', amount: '200.00', method: 'transfer', reason: 'Avans tratament (fictiv)' });
  check('advance on patient B saved', advance.status === 201);
  t = await totals(panelB);
  check('advance shown as credit with no services', t.services === '0.00 RON' && t.balance === '200.00 RON credit (avans al pacientului)', t);
  await shot(reception.page, 'advance-patient-b');

  // ---- Manager: reverse the card payment with a reason, then an adjustment ----
  const manager = await login(creds.manager, 'manager'); activePage = manager.page;
  panel = await openAccount(manager.page, patientA);
  const cardEntry = panel.locator('[data-entry-kind="payment"]').filter({ hasText: 'Card' });
  const cardId = await cardEntry.getAttribute('data-entry-id');
  await cardEntry.getByRole('button', { name: 'Anulează mișcarea' }).click();
  check('confirm disabled until a reason is given', await cardEntry.getByRole('button', { name: 'Confirmă anularea' }).isDisabled());
  await cardEntry.getByLabel(`Motiv anulare ${cardId}`).fill('Plată card dublată (fictiv)');
  const revDone = manager.page.waitForResponse(r => r.request().method() === 'POST' && /\/reverse$/.test(new URL(r.url()).pathname));
  const beforeRev = summary.network.reversePosts;
  await cardEntry.getByRole('button', { name: 'Confirmă anularea' }).dblclick();
  check('reversal saved once', (await revDone).status() === 201 && summary.network.reversePosts - beforeRev === 1, { posts: summary.network.reversePosts - beforeRev });
  await panel.locator(`[data-entry-id="${cardId}"]`).getByTestId('entry-reversed').waitFor();
  t = await totals(panel);
  check('reversal neutralizes the payment; original kept and marked', t.balance === '0.00 RON · achitat'
    && (await panel.locator(`[data-entry-id="${cardId}"]`).innerText()).includes('Plată card dublată'), t);
  const again = await (await apiSession(creds.manager, 'manager api')).call('POST', `/finance/entries/${cardId}/reverse`, { reason: 'a doua', client_request_id: crypto.randomUUID() });
  check('second reversal of the same payment refused (409)', again.status === 409, { status: again.status });
  const adjust = await record(manager.page, panel, { kind: 'adjustment_debit', amount: '15.00', reason: 'Materiale suplimentare (fictiv)' });
  check('manager adjustment saved', adjust.status === 201);
  t = await totals(panel);
  check('balance after adjustment', t.balance === '15.00 RON de plată', t);
  await shot(manager.page, 'manager-account');

  // ---- Reload persistence ----
  await manager.page.reload({ waitUntil: 'domcontentloaded' });
  panel = panelOf(manager.page);
  await panel.getByTestId('account-balance').waitFor();
  t = await totals(panel);
  const kindsShown = await panel.locator('[data-entry-kind]').evaluateAll(n => n.map(x => x.getAttribute('data-entry-kind')));
  check('after reload: totals, reversal and adjustment persist', t.balance === '15.00 RON de plată' && t.services === `${money(owed)} RON`
    && kindsShown.filter(k => k === 'reversal').length === 1 && kindsShown.includes('adjustment_debit')
    && await panel.locator(`[data-entry-id="${cardId}"]`).getByTestId('entry-reversed').count() === 1, { kindsShown });

  // ---- Doctor: reads, cannot write ----
  const doctor = await login(creds.doctor, 'doctor'); activePage = doctor.page;
  panel = await openAccount(doctor.page, patientA);
  check('doctor sees the same balance', (await totals(panel)).balance === '15.00 RON de plată');
  check('doctor has no form and no reversal buttons', await panel.getByRole('form', { name: 'Mișcare nouă' }).count() === 0
    && await panel.getByRole('button', { name: 'Anulează mișcarea' }).count() === 0);
  const doctorWrite = await doctorApi.call('POST', `/finance/patients/${patientA.id}/entries`, { kind: 'payment', amount: '1.00', method: 'cash', client_request_id: crypto.randomUUID() });
  const doctorDaily = await doctorApi.call('GET', '/finance/daily?date=2026-11-19');
  check('doctor write and daily report refused (403)', doctorWrite.status === 403 && doctorDaily.status === 403, { write: doctorWrite.status, daily: doctorDaily.status });
  check('doctor has no daily report link', await doctor.page.getByRole('link', { name: 'Încasări zi' }).count() === 0);

  // ---- Another clinic ----
  const otherCtx = await request.newContext();
  const otherEmail = `t13-other-${tag}-${Math.random().toString(16).slice(2, 8)}@example.com`;
  const otherPassword = `fictiv-${Math.random().toString(36).slice(2)}-${Date.now()}`;
  const reg = await otherCtx.post(`${api}/auth/register`, { data: { clinic_name: `Altă clinică T13 ${tag}`, full_name: 'Manager altă clinică', email: otherEmail, password: otherPassword } });
  const other = session(otherCtx, (await reg.json()).access_token);
  const oDenied = {
    account: (await other.call('GET', `/finance/patients/${patientA.id}/account`)).status,
    entry: (await other.call('POST', `/finance/patients/${patientA.id}/entries`, { kind: 'payment', amount: '1.00', method: 'cash', client_request_id: crypto.randomUUID() })).status,
    reverse: (await other.call('POST', `/finance/entries/${cardId}/reverse`, { reason: 'x', client_request_id: crypto.randomUUID() })).status,
    visit: (await other.call('GET', `/finance/appointments/${visitId}/summary`)).status,
  };
  check('other clinic gets 404 on account, entries, reverse and visit summary', Object.values(oDenied).every(s => s === 404), oDenied);
  const otherUi = await login({ email: otherEmail, password: otherPassword }, 'other-clinic'); activePage = otherUi.page;
  await otherUi.page.goto(`${web}/patients/${patientA.id}`, { waitUntil: 'domcontentloaded' });
  await otherUi.page.waitForTimeout(1500);
  check('other clinic UI shows no account', await panelOf(otherUi.page).count() === 0 && !(await otherUi.page.locator('body').innerText()).includes(patientA.last_name));
  await otherCtx.dispose();

  // ---- Daily report (reception) ----
  activePage = reception.page;
  await reception.page.getByRole('link', { name: 'Încasări zi' }).click();
  await reception.page.getByRole('heading', { name: 'Încasări zi' }).waitFor();
  const today = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: tz }).format(new Date());
  check('daily report opens on the clinic day', await reception.page.getByLabel('Ziua raportului').inputValue() === today, { today });
  await reception.page.getByTestId('daily-net').waitFor();
  const daily = (await receptionApi.call('GET', `/finance/daily?date=${today}`)).body;
  const mine = daily.entries.filter(e => [patientA.id, patientB.id].includes(e.patient_id));
  const sumBy = method => mine.filter(e => e.method === method).reduce((s, e) => s + cents(e.signed_amount), 0);
  check('daily report nets this run per method (cash = visit, card 50-50 = 0, transfer = advance)',
    sumBy('cash') === owed && sumBy('card') === 0 && sumBy('transfer') === 20000 && mine.length === 4, { cash: money(sumBy('cash')), card: money(sumBy('card')), transfer: money(sumBy('transfer')), rows: mine.length });
  const uiNet = (await reception.page.getByTestId('daily-net').innerText()).trim();
  check('UI daily total equals the API net', uiNet === daily.net, { uiNet, apiNet: daily.net });
  await shot(reception.page, 'daily-report');

  check('no browser page errors', summary.pageErrors.length === 0, { pageErrors: summary.pageErrors });
  summary.result = 'PASS';
} catch (error) {
  summary.result = 'FAIL';
  summary.errors.push(String(error.message ?? error).slice(0, 1200));
  if (activePage) await shot(activePage, 'failure').catch(() => {});
  process.exitCode = 1;
} finally {
  for (const context of contexts) await context.close().catch(() => {});
  await doctorApi.ctx.dispose();
  await receptionApi.ctx.dispose();
  await browser.close();
  const passed = summary.checks.filter(c => c.passed).length;
  summary.totals = { passed, total: summary.checks.length };
  await fs.writeFile(path.join(evidenceDir, 't13-browser-summary.json'), JSON.stringify(summary, null, 2), { mode: 0o600 });
  console.log(`T13 browser ${summary.result}: ${passed}/${summary.checks.length} checks · page errors ${summary.pageErrors.length} · console/http errors ${summary.consoleErrors.length} · evidence ${evidenceDir}`);
  if (summary.errors.length) console.log(summary.errors.join('\n'));
}
