// Walkthrough of docs/pilot/DEMO.md on the SEEDED fictional data, as reception, doctor and manager.
// Run against a runtime started by demo.sh (or a restored copy of it). Credentials come only from the
// local 600 file named by PILOT_CREDENTIALS and never reach the summary. Console output and failed
// requests are recorded and classified: anything not listed as expected fails the run.
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const required = ['DENTACARE_PILOT_HOME', 'DENTACARE_APP_SHA', 'PILOT_WEB_URL', 'PILOT_API_URL', 'PILOT_CREDENTIALS', 'PLAYWRIGHT_MODULE', 'CHROMIUM_EXECUTABLE'];
for (const key of required) if (!process.env[key]) throw new Error(`Missing ${key}`);
const { chromium, request } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const creds = JSON.parse(await fs.readFile(process.env.PILOT_CREDENTIALS, 'utf8'));
const web = process.env.PILOT_WEB_URL.replace(/\/$/, '');
const api = process.env.PILOT_API_URL.replace(/\/$/, '');
const BROWSER_TZ = process.env.DEMO_BROWSER_TZ || 'Europe/Bucharest';
const evidenceDir = process.env.DEMO_EVIDENCE_DIR || path.join(process.env.DENTACARE_PILOT_HOME, 'browser-evidence', 'demo');
await fs.mkdir(evidenceDir, { recursive: true, mode: 0o700 });
const runId = new Date().toISOString().replaceAll(':', '-');
const tag = runId.slice(11, 19).replaceAll('-', '');
const summary = { task: 'T33-walkthrough', appSha: process.env.DENTACARE_APP_SHA, web, api, browser: null, browserTimezone: BROWSER_TZ,
  clinicTimezone: null, checks: [], artifacts: [], pageErrors: [], consoleErrors: [], classified: { expected: [], unexpected: [] }, errors: [] };
const check = (name, ok, details = {}) => { summary.checks.push({ name, passed: Boolean(ok), ...details }); if (!ok) throw new Error(`Walkthrough failed: ${name} ${JSON.stringify(details)}`); };
const SHOTS_MAX = 6;
const shot = async (page, name) => {
  if (summary.artifacts.length >= SHOTS_MAX) return;
  const file = path.join(evidenceDir, `demo-${name}.png`); await page.screenshot({ path: file, fullPage: true }); summary.artifacts.push(path.basename(file));
};
const idPath = url => { try { return new URL(url).pathname.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id'); } catch { return null; } };
const dayInClinic = (tz, offsetDays = 0) => new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: tz }).format(new Date(Date.now() + offsetDays * 86400000));

// Seed identifiers (fixed, derived in seed.py with uuid5) are resolved through the API by name: no id is hard-coded here.
async function apiSession(account, label) {
  const ctx = await request.newContext();
  const login = await ctx.post(`${api}/auth/login`, { data: { email: account.email, password: account.password } });
  if (!login.ok()) throw new Error(`API login failed for ${label}: ${login.status()}`);
  const token = (await login.json()).access_token;
  const call = async (method, url, data) => {
    const response = await ctx.fetch(`${api}${url}`, { method, data, headers: { Authorization: `Bearer ${token}` } });
    return { status: response.status(), body: await response.json().catch(() => null) };
  };
  return { ctx, call, token };
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
  await page.goto(web, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill(account.email);
  await page.getByLabel('Password').fill(account.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Patients', exact: true }).waitFor();
  return page;
}
const navNames = async page => (await page.locator('aside nav a').allInnerTexts()).map(text => text.trim());
const mainText = page => page.locator('main.content').innerText();
const respond = (page, method, test) => page.waitForResponse(r => r.request().method() === method && test(new URL(r.url()).pathname));
const plansOf = page => page.getByRole('region', { name: 'Planuri de tratament' });
const docsOf = page => page.getByRole('region', { name: 'Documente și imagini' });
const accountOf = page => page.getByRole('region', { name: 'Cont pacient' });
const totals = async panel => ({
  services: (await panel.getByTestId('account-services').innerText()).trim(),
  paid: (await panel.getByTestId('account-paid').innerText()).trim(),
  balance: (await panel.getByTestId('account-balance').innerText()).trim(),
});

const doctorApi = await apiSession(creds.doctor, 'doctor');
let activePage = null;
try {
  // ---- Seed state through the API (names only; no ids copied from anywhere) ----
  const clinic = (await doctorApi.call('GET', '/clinic')).body;
  summary.clinicTimezone = clinic.timezone;
  const tz = clinic.timezone;
  check('clinic zone is Europe/Bucharest (the demo clinic)', tz === 'Europe/Bucharest', { tz });
  const patients = (await doctorApi.call('GET', '/patients?limit=100')).body;
  const byName = first => patients.find(p => p.first_name === first && p.last_name === { Ioana: 'Exemplu', Andrei: 'Model', Elena: 'Test' }[first]);
  const [ioana, andrei, elena] = ['Ioana', 'Andrei', 'Elena'].map(byName);
  check('the three seeded demo patients exist', Boolean(ioana && andrei && elena));
  const today = dayInClinic(tz);
  const dayAppointments = (await doctorApi.call('GET', `/appointments?day=${today}`)).body;
  const visitIoana = dayAppointments.find(a => a.patient_id === ioana.id && a.appointment_type === 'Obturație demonstrativă');
  const visitElena = dayAppointments.find(a => a.patient_id === elena.id && a.appointment_type === 'Igienizare demonstrativă');
  check('seed placed visits on today in the clinic zone', Boolean(visitIoana && visitElena) && dayAppointments.length >= 3, { today, visits: dayAppointments.length });

  // ===================== RECEPȚIE =====================
  const reception = await login(creds.reception, 'reception'); activePage = reception;
  const receptionNav = await navNames(reception);
  check('reception menu: patients, agenda, catalog, daily cash; no setup/staff', ['Patients', 'Appointments', 'Procedure catalog', 'Încasări zi'].every(n => receptionNav.includes(n)) && !receptionNav.includes('Clinic setup') && !receptionNav.includes('Staff'), { nav: receptionNav });

  await reception.getByRole('link', { name: 'Appointments', exact: true }).click();
  await reception.getByText('Ioana Exemplu').first().waitFor();
  const agenda = await mainText(reception);
  check('agenda today shows the three seeded visits at their clinic-zone hours', ['09:00', '11:00', '14:00', 'Ioana Exemplu', 'Andrei Model', 'Elena Test'].every(t => agenda.includes(t)), { hasIoana: agenda.includes('Ioana Exemplu') });
  check('agenda shows both rooms and the visit statuses', /Cabinet Demo 2/.test(agenda) && /in progress/.test(agenda) && /confirmed/.test(agenda) && /scheduled/.test(agenda));
  await shot(reception, 'reception-agenda');

  // New appointment in the UI (demo.md step): fixed far-future weekday, retried on conflict.
  await reception.getByRole('button', { name: 'New appointment' }).click();
  const form = reception.locator('form').filter({ has: reception.getByRole('heading', { name: 'New appointment' }) });
  await form.waitFor();
  await form.getByLabel('Find patient').fill('Elena');
  const selects = form.locator('select'); // patient, doctor, assistant, room, DST (labels wrap the options)
  await selects.nth(0).selectOption({ label: 'Elena Test' });
  await selects.nth(1).selectOption({ index: 1 });
  await selects.nth(3).selectOption({ label: 'Cabinet Demo 2' });
  await form.getByLabel('Appointment type').fill(`Control programat în ghid ${tag}`);
  // Pick a slot nobody uses (checked through the API), so the booking is one clean 201 and no conflict noise reaches the console.
  let slot = null;
  for (let ahead = 40; ahead < 200 && !slot; ahead++) {
    const dayText = dayInClinic(tz, ahead);
    if ([0, 6].includes(new Date(`${dayText}T12:00:00Z`).getUTCDay())) continue;
    const taken = (await doctorApi.call('GET', `/appointments?day=${dayText}`)).body.map(a => a.local_start.slice(11, 16));
    const hour = ['09:00', '10:00', '11:00', '15:00', '16:00'].find(h => !taken.includes(h));
    if (hour) slot = `${dayText}T${hour}`;
  }
  check('a free clinic-local slot was found for the demo booking', Boolean(slot), { slot });
  await form.getByLabel('Clinic-local start').fill(slot);
  await form.getByLabel('Duration (minutes)').fill('30');
  const createdAppointment = respond(reception, 'POST', p => p === '/api/appointments');
  await form.getByRole('button', { name: 'Create appointment' }).click();
  const bookingResponse = await createdAppointment;
  const booked = { status: bookingResponse.status(), slot, body: await bookingResponse.json().catch(() => null) };
  check('reception books a new appointment in the UI', booked?.status === 201, { status: booked?.status, detail: booked?.body?.detail });
  check('the booked time is read back in the clinic zone (UI input = clinic wall time)', booked.body.local_start.startsWith(booked.slot), { sent: booked.slot, localStart: booked.body.local_start });

  // Collect in the patient account.
  await reception.goto(`${web}/patients/${ioana.id}`, { waitUntil: 'domcontentloaded' });
  const account = accountOf(reception);
  await account.getByTestId('account-balance').waitFor();
  const before = await totals(account);
  const num = text => Number((text.match(/-?\d+(?:\.\d+)?/) ?? ['NaN'])[0]);
  // First pass on a pristine seed: paid 200.00 and balance 200.00 due. Later passes on the same DB add 100.00 each, so only the invariants are asserted.
  check('Ioana account: services 400.00 and paid net at least 200.00 (the reversed 50.00 is excluded)', num(before.services) === 400 && num(before.paid) >= 200 && /de plată|achitat|credit/.test(before.balance), before);
  check('the reversed movement is visible with its reason (history is not rewritten)', await account.getByTestId('entry-reversed').count() >= 1 && (await account.innerText()).includes('suma a fost introdusă pe pacientul greșit'));
  await account.getByLabel('Tip mișcare').selectOption('payment');
  await account.getByLabel('Sumă', { exact: true }).fill('100.00');
  await account.getByLabel('Metodă', { exact: true }).selectOption('cash');
  await account.getByLabel('Vizită asociată').selectOption({ index: 1 });
  await account.getByLabel('Motiv', { exact: true }).fill(`Încasare demo ghid ${tag}`);
  const paid = respond(reception, 'POST', p => /\/api\/finance\/patients\/[^/]+\/entries$/.test(p));
  await account.getByRole('button', { name: 'Înregistrează' }).click();
  check('reception records a payment (201)', (await paid).status() === 201);
  await account.getByTestId('finance-form-saved').waitFor();
  const expectedPaid = (num(before.paid) + 100).toFixed(2);
  await reception.waitForFunction(expected => (document.querySelector('[data-testid="account-paid"]')?.textContent ?? '').includes(expected), expectedPaid);
  const after = await totals(account);
  check('paid net increased by exactly the recorded 100.00 and the balance text was recomputed', num(after.paid) === num(before.paid) + 100 && after.balance !== before.balance, { before, after });
  await shot(reception, 'reception-account');

  await reception.getByRole('link', { name: 'Încasări zi', exact: true }).click();
  await reception.getByRole('heading', { name: 'Încasări zi' }).waitFor();
  await reception.getByText('Total net').first().waitFor();
  await reception.getByText('Anulare').first().waitFor();
  const daily = await mainText(reception);
  check('daily cash page: per-method net, total, reversal listed, explicitly not fiscal', /Total net/.test(daily) && /Numerar/.test(daily) && /Card/.test(daily) && /Anulare/.test(daily) && /NU ESTE RAPORT FISCAL/i.test(daily), { text: daily.slice(0, 160) });

  // ===================== MEDIC =====================
  const doctor = await login(creds.doctor, 'doctor'); activePage = doctor;
  const doctorNav = await navNames(doctor);
  check('doctor menu has no daily cash, setup or staff', !doctorNav.includes('Încasări zi') && !doctorNav.includes('Clinic setup') && !doctorNav.includes('Staff'), { nav: doctorNav });

  // Visit with a measured procedure created by the seed: opened, not started by opening.
  await doctor.goto(`${web}/appointments/${visitIoana.id}/visit`, { waitUntil: 'domcontentloaded' });
  await doctor.getByRole('heading', { name: 'Performed procedures', exact: true }).waitFor();
  await doctor.locator('article.procedure-card').first().waitFor();
  await doctor.getByText('Servicii efectuate la vizită').waitFor();
  const visitText = await mainText(doctor);
  check('visit shows the completed REST-DEMO on 16 with the price snapshot and a recorded active time',
    /REST-DEMO/.test(visitText) && /FDI 16/.test(visitText) && /400\.00 RON/.test(visitText) && !/No active time recorded yet/.test(visitText), { text: visitText.slice(0, 400) });
  check('completed procedure did not close the visit (still in progress, closing is separate)', /in progress/.test(visitText) && (await doctor.getByRole('button', { name: 'Close visit' }).count()) === 1);
  await shot(doctor, 'doctor-visit');

  // Odontogram: seeded finding on 16 and missing teeth.
  await doctor.goto(`${web}/patients/${ioana.id}`, { waitUntil: 'domcontentloaded' });
  await doctor.locator('.patient-odontogram [data-tooth-id="16"]').waitFor();
  check('odontogram shows 1.8 and 2.8 as missing', (await doctor.locator('.patient-odontogram [data-tooth-id="18"]').innerText()).includes('Lipsă') && (await doctor.locator('.patient-odontogram [data-tooth-id="28"]').innerText()).includes('Lipsă'));
  await doctor.locator('.patient-odontogram [data-tooth-id="16"]').click();
  await doctor.locator('.og-finding').first().waitFor();
  const findingText = await doctor.locator('.og-finding').first().innerText();
  check('selecting tooth 16 shows the finalized seeded finding (carie, occlusal) without starting any timer', /Finalizată/i.test(findingText) && /Carie ocluzală profundă/.test(findingText));

  // Procedure from the tooth on today's Elena visit, with the explicit timer.
  await doctor.goto(`${web}/patients/${elena.id}`, { waitUntil: 'domcontentloaded' });
  await doctor.locator('.patient-odontogram [data-tooth-id="36"]').waitFor();
  const procsBefore = (await doctorApi.call('GET', `/performed-procedures?patient_id=${elena.id}&limit=200`)).body.length;
  await doctor.locator('.patient-odontogram [data-tooth-id="36"]').click();
  await doctor.getByRole('button', { name: 'Add performed procedure for this tooth' }).click();
  const entry = doctor.getByRole('region', { name: 'Add procedure for selected tooth' });
  await entry.getByLabel('Procedure visit').locator(`option[value="${visitElena.id}"]`).waitFor({ state: 'attached' });
  await entry.getByLabel('Procedure visit').selectOption(visitElena.id);
  const procForm = entry.locator('form.t07-procedure-form');
  await procForm.getByLabel('Procedure from active catalog').selectOption({ label: 'PROF-DEMO · Igienizare demonstrativă · 250.00 RON' });
  check('tooth 36 is preselected from the odontogram', await procForm.getByLabel('Procedure tooth').inputValue() === '36');
  const note = `Igienizare ghid ${tag}`;
  await procForm.getByLabel('Procedure notes (optional)').fill(note);
  const created = respond(doctor, 'POST', p => p === '/api/performed-procedures');
  await procForm.getByRole('button', { name: 'Save procedure' }).click();
  const procedure = await (await created).json();
  check('procedure from the tooth is saved pending, no timer started', procedure.status === 'pending' && procedure.tooth_fdi === '36' && (await doctorApi.call('GET', `/performed-procedures/${procedure.id}/segments`)).body.length === 0);
  await doctor.goto(`${web}/appointments/${visitElena.id}/visit`, { waitUntil: 'domcontentloaded' });
  const card = doctor.locator('article.procedure-card').filter({ hasText: note });
  await card.waitFor();
  for (const [transition, label, expected] of [['start', 'Start', 'running'], ['pause', 'Pause', 'paused'], ['resume', 'Resume', 'running'], ['complete', 'Complete', 'completed']]) {
    const r = respond(doctor, 'POST', p => p.endsWith(`/performed-procedures/${procedure.id}/${transition}`));
    await card.getByRole('button', { name: label, exact: true }).click();
    const response = await r;
    check(`timer ${transition} -> ${expected}`, response.status() === 200 && (await response.json()).status === expected);
    if (transition === 'pause') await doctor.waitForTimeout(1100);
  }
  const elenaProcs = (await doctorApi.call('GET', `/performed-procedures?patient_id=${elena.id}&limit=200`)).body;
  check('exactly one new procedure was added, and the visit is still open', elenaProcs.length === procsBefore + 1 && (await doctorApi.call('GET', `/appointments/${visitElena.id}`)).body.status !== 'completed');

  // Plan.
  await doctor.goto(`${web}/patients/${ioana.id}`, { waitUntil: 'domcontentloaded' });
  const plans = plansOf(doctor);
  await plans.getByRole('article', { name: 'Rezumat Plan restaurativ demonstrativ' }).getByRole('button', { name: 'Deschide planul' }).click();
  await plans.getByTestId('plan-status').waitFor();
  const planText = await plans.innerText();
  check('plan: two stages, accepted version, first item done and linked to the performed procedure', /Etapa 1/.test(planText) && /Etapa 2/.test(planText) && (await plans.getByTestId('accepted-version').count()) >= 1 && (await plans.getByTestId('item-progress').filter({ hasText: 'Efectuat' }).count()) >= 1);
  await shot(doctor, 'doctor-plan');

  // Periodontal comparison between the two seeded finalized sessions.
  await doctor.goto(`${web}/patients/${andrei.id}`, { waitUntil: 'domcontentloaded' });
  const paro = doctor.getByRole('region').filter({ has: doctor.getByRole('heading', { name: 'Evaluare parodontală' }) });
  await paro.getByLabel('Istoric').waitFor();
  let history = [];
  for (let i = 0; i < 40 && history.filter(text => /Finalizată/.test(text)).length < 2; i++) {
    history = await paro.getByLabel('Istoric').locator('option').allInnerTexts();
    if (history.filter(text => /Finalizată/.test(text)).length < 2) await doctor.waitForTimeout(250);
  }
  check('periodontal history lists two finalized sessions', history.filter(text => /Finalizată/.test(text)).length === 2, { options: history.length });
  const options = await paro.getByLabel('Prima evaluare').locator('option').evaluateAll(list => list.map(o => ({ value: o.value, label: o.textContent })));
  const older = options.find(o => o.label.includes(dayInClinic(tz, -90)));
  const newer = options.find(o => o.label.includes(dayInClinic(tz, -2)));
  check('the two seeded sessions are selectable by clinical date', Boolean(older && newer), { options: options.length });
  await paro.getByLabel('Prima evaluare').selectOption(older.value);
  await paro.getByLabel('A doua evaluare').selectOption(newer.value);
  await paro.getByRole('button', { name: 'Compară' }).click();
  const comparison = doctor.locator('.periodontal-comparison-results');
  await comparison.getByText(/BOP a doua evaluare:/).first().waitFor();
  check('comparison shows the persisted probing-depth improvement (Δ -1)', (await comparison.innerText()).includes('Δ -1'));
  await shot(doctor, 'doctor-periodontal-comparison');

  // Documents: Elena has a radiograph, a photo and a document; view with zoom, bytes match the stored hash.
  await doctor.goto(`${web}/patients/${elena.id}`, { waitUntil: 'domcontentloaded' });
  const docs = docsOf(doctor);
  const radiograph = docs.locator('article.doc-item', { hasText: 'radiografie-36-demo.png' });
  await radiograph.waitFor();
  check('Elena has the radiograph, the photo and the consent document', (await docs.locator('article.doc-item', { hasText: 'fotografie-intraorala-demo.jpg' }).count()) === 1 && (await docs.locator('article.doc-item', { hasText: 'consimtamant-demo.pdf' }).count()) === 1);
  await radiograph.getByRole('button', { name: 'Vizualizează' }).click();
  const viewer = docs.getByLabel('Vizualizare imagine');
  await doctor.waitForFunction(() => { const el = document.querySelector('.doc-viewer img'); return el && el.complete && el.naturalWidth > 0; });
  await viewer.getByRole('button', { name: 'Mărește' }).click();
  check('zoom works on the authorized image', (await viewer.getByTestId('zoom-level').innerText()) === '125%');
  const stored = (await doctorApi.call('GET', `/documents/patients/${elena.id}`)).body.find(d => d.original_filename === 'radiografie-36-demo.png');
  const content = await doctorApi.ctx.fetch(`${api}/documents/${stored.id}/content`, { headers: { Authorization: `Bearer ${doctorApi.token}` } });
  check('downloaded bytes hash equals the stored SHA-256', createHash('sha256').update(await content.body()).digest('hex') === stored.sha256);
  await shot(doctor, 'doctor-document-zoom');

  // The two known UX defects: unknown patient, favicon.
  const missing = await doctor.goto(`${web}/patients/00000000-0000-4000-8000-000000000000`, { waitUntil: 'domcontentloaded' });
  await doctor.getByRole('alert').filter({ hasText: 'Pacient inexistent sau fără acces' }).waitFor({ timeout: 8000 });
  check('unknown/foreign patient id shows the clear message quickly, not "Loading patient"', !(await mainText(doctor)).includes('Loading patient') && missing.status() === 200);
  check('favicon is linked and served', (await doctor.locator('link[rel="icon"]').count()) === 1 && (await doctor.request.get(`${web}/favicon.svg`)).status() === 200);

  // ===================== MANAGER =====================
  const manager = await login(creds.manager, 'manager'); activePage = manager;
  const managerNav = await navNames(manager);
  check('manager menu includes setup, staff, catalog and daily cash', ['Clinic setup', 'Staff', 'Procedure catalog', 'Încasări zi'].every(n => managerNav.includes(n)), { nav: managerNav });
  await manager.getByRole('link', { name: 'Clinic setup', exact: true }).click();
  await manager.getByRole('heading', { name: 'Clinic setup' }).waitFor();
  await manager.getByRole('heading', { name: 'Treatment rooms' }).waitFor();
  await manager.getByRole('button', { name: 'Rename' }).first().waitFor();
  await manager.getByText('Clinic timezone: Europe/Bucharest').first().waitFor();
  const setup = await mainText(manager);
  check('setup shows the clinic zone, both rooms and working hours', /Europe\/Bucharest/.test(setup) && /Cabinet Demo 1/.test(setup) && /Cabinet Demo 2/.test(setup) && /Working hours/.test(setup));
  await manager.getByRole('link', { name: 'Procedure catalog', exact: true }).click();
  await manager.getByRole('heading', { name: 'Procedure catalog' }).waitFor();
  await manager.getByText('REST-DEMO').first().waitFor();
  const catalogText = await mainText(manager);
  check('catalog lists the three demo services with prices', ['CONS-DEMO', 'PROF-DEMO', 'REST-DEMO'].every(c => catalogText.includes(c)) && /400\.00/.test(catalogText));

  await manager.goto(`${web}/patients/${elena.id}`, { waitUntil: 'domcontentloaded' });
  const mgrAccount = accountOf(manager);
  await mgrAccount.getByTestId('account-balance').waitFor();
  const mgrBefore = await totals(mgrAccount);
  await mgrAccount.getByLabel('Tip mișcare').selectOption('adjustment_debit');
  await mgrAccount.getByLabel('Sumă', { exact: true }).fill('10.00');
  await mgrAccount.getByLabel('Motiv', { exact: true }).fill(`Ajustare demo ghid ${tag}`);
  const adjust = respond(manager, 'POST', p => /\/api\/finance\/patients\/[^/]+\/entries$/.test(p));
  await mgrAccount.getByRole('button', { name: 'Înregistrează' }).click();
  check('manager records an adjustment with a reason (201)', (await adjust).status() === 201);
  await mgrAccount.getByTestId('finance-form-saved').waitFor();
  const adjustedEntry = mgrAccount.locator('article', { hasText: `Ajustare demo ghid ${tag}` }).first();
  await adjustedEntry.waitFor();
  await adjustedEntry.getByRole('button', { name: 'Anulează mișcarea' }).click();
  await mgrAccount.getByLabel(/Motiv/).last().fill('Anulare demo: ajustare introdusă pentru ghid');
  const reverse = respond(manager, 'POST', p => /\/api\/finance\/entries\/[^/]+\/reverse$/.test(p));
  await mgrAccount.getByRole('button', { name: 'Confirmă anularea' }).click();
  check('manager reverses it with a reason (201)', (await reverse).status() === 201);
  await manager.waitForFunction(() => document.querySelectorAll('[data-testid="entry-reversed"]').length >= 1);
  const mgrAfter = await totals(mgrAccount);
  check('after adjustment + reversal the balance equals the starting balance (net effect zero, history kept)', JSON.stringify(mgrAfter) === JSON.stringify(mgrBefore), { before: mgrBefore, after: mgrAfter });
  await shot(manager, 'manager-adjustment-reversal');

  // ===================== Console classification =====================
  // Expected: exactly the deliberate 404 for the unknown patient id (browser logs it as a resource error).
  const isExpected = e => e.user === 'doctor' && ((e.httpStatus === 404 && e.path === '/api/patients/:id') || (/404/.test(e.text ?? '') && e.sourcePath === '/api/patients/:id'));
  summary.classified.expected = summary.consoleErrors.filter(isExpected);
  summary.classified.unexpected = summary.consoleErrors.filter(e => !isExpected(e));
  check('no unexpected console errors or failed requests during the walkthrough', summary.classified.unexpected.length === 0 && summary.pageErrors.length === 0, { unexpected: summary.classified.unexpected.slice(0, 5), pageErrors: summary.pageErrors.length });
} catch (error) {
  summary.errors.push(String(error.message).slice(0, 600));
  if (activePage) { try { await shot(activePage, 'failure'); } catch { /* best effort */ } }
  process.exitCode = 1;
} finally {
  for (const context of contexts) await context.close().catch(() => {});
  await browser.close();
  await doctorApi.ctx.dispose();
  summary.passed = summary.checks.filter(c => c.passed).length;
  summary.total = summary.checks.length;
  await fs.writeFile(path.join(evidenceDir, 'demo-walkthrough-LATEST.json'), JSON.stringify(summary, null, 2) + '\n', { mode: 0o600 });
  console.log(JSON.stringify({ task: summary.task, passed: summary.passed, total: summary.total, errors: summary.errors, pageErrors: summary.pageErrors.length, consoleErrors: summary.consoleErrors.length, unexpected: summary.classified.unexpected.length }));
}
