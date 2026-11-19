// Consolidated pilot browser acceptance (T05-T11 on one record) against a running fictional runtime
// (real API + SQLite + local protected document storage). One fictional patient goes through:
// appointment -> visit -> T07 procedure from a tooth with explicit timer start/pause/resume/complete,
// visit still open until a separate close -> odontogram finding -> T08 plan linked to the performed
// procedure -> T09/T10 finalized assessment + follow-up + comparison -> T11 document on visit/tooth with
// zoom -> full reload persistence -> manager/reception on the same record -> second clinic isolation.
// The browser runs in UTC while the clinic is Europe/Bucharest, so clinic-time display is observable.
// Patient + appointment are prepared by the reception account through the real T01/T02 API; every
// clinical action is done in the UI. Credentials and tokens are never written to the summary.
import fs from 'node:fs/promises';
import path from 'node:path';
import zlib from 'node:zlib';
import { pathToFileURL } from 'node:url';

const required = ['DENTACARE_PILOT_HOME', 'DENTACARE_APP_SHA', 'PILOT_WEB_URL', 'PILOT_API_URL', 'PILOT_CREDENTIALS', 'PLAYWRIGHT_MODULE', 'CHROMIUM_EXECUTABLE'];
for (const key of required) if (!process.env[key]) throw new Error(`Missing ${key}`);
const { chromium, request } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const creds = JSON.parse(await fs.readFile(process.env.PILOT_CREDENTIALS, 'utf8'));
const web = process.env.PILOT_WEB_URL.replace(/\/$/, '');
const api = process.env.PILOT_API_URL.replace(/\/$/, '');
const evidenceDir = process.env.CONSOLIDATED_EVIDENCE_DIR || path.join(process.env.DENTACARE_PILOT_HOME, 'browser-evidence', 'consolidated');
const fixtureDir = path.join(process.env.DENTACARE_PILOT_HOME, 'consolidated-fixtures');
await fs.mkdir(evidenceDir, { recursive: true, mode: 0o700 });
await fs.mkdir(fixtureDir, { recursive: true, mode: 0o700 });
const runId = new Date().toISOString().replaceAll(':', '-');
const tag = runId.slice(11, 19).replaceAll('-', '');
// Default UTC keeps every original assertion. CONSOLIDATED_BROWSER_TZ (e.g. Europe/Bucharest) runs the same flow with the
// browser set to the clinic zone: the two "differs from the browser zone" checks cannot hold there by construction and are
// skipped; the display checks (clinic time shown, API timestamps carry an offset) still apply.
const BROWSER_TZ = process.env.CONSOLIDATED_BROWSER_TZ || 'UTC';
const SAME_ZONE_RUN = Boolean(process.env.CONSOLIDATED_BROWSER_TZ);
const summary = { task: 'consolidated T05-T11', appSha: process.env.DENTACARE_APP_SHA, web, api, browser: null, browserTimezone: BROWSER_TZ, clinicTimezone: null,
  checks: [], artifacts: [], pageErrors: [], consoleErrors: [], errors: [], ids: {} };
const check = (name, ok, details = {}) => { summary.checks.push({ name, passed: Boolean(ok), ...details }); if (!ok) throw new Error(`Acceptance failed: ${name} ${JSON.stringify(details)}`); };
const shot = async (page, name) => { const file = path.join(evidenceDir, `consolidated-${name}.png`); await page.screenshot({ path: file, fullPage: true }); summary.artifacts.push(path.basename(file)); };
const idPath = url => { try { return new URL(url).pathname.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id'); } catch { return null; } };

// ---- Fictional radiograph (real PNG) ----
function crc32(buf) { let c, crc = 0xffffffff; for (const b of buf) { c = (crc ^ b) & 0xff; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; crc = (crc >>> 8) ^ c; } return (crc ^ 0xffffffff) >>> 0; }
function png(width, height, shade) {
  const chunk = (type, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]); const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, crc]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr[8] = 8; ihdr[9] = 0;
  const rows = [];
  for (let y = 0; y < height; y++) { const row = Buffer.alloc(width + 1); for (let x = 0; x < width; x++) row[x + 1] = ((x >> 5) + (y >> 5)) % 2 ? shade : 255 - shade; rows.push(row); }
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(Buffer.concat(rows))), chunk('IEND', Buffer.alloc(0))]);
}
const rx = { name: `rx-consolidat-16-${tag}.png`, data: png(1400, 900, 70) };
rx.path = path.join(fixtureDir, rx.name);
await fs.writeFile(rx.path, rx.data, { mode: 0o600 });

async function apiSession(account, label) {
  const ctx = await request.newContext();
  const login = await ctx.post(`${api}/auth/login`, { data: { email: account.email, password: account.password } });
  if (!login.ok()) throw new Error(`API login failed for ${label}: ${login.status()}`);
  const token = (await login.json()).access_token;
  return sessionFromToken(ctx, token);
}
function sessionFromToken(ctx, token) {
  const call = async (method, url, data, raw = false) => {
    const response = await ctx.fetch(`${api}${url}`, { method, data, headers: { Authorization: `Bearer ${token}` } });
    return { status: response.status(), headers: response.headers(), body: raw ? await response.body() : await response.json().catch(() => null) };
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
  await page.goto(web, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill(account.email);
  await page.getByLabel('Password').fill(account.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Patients', exact: true }).waitFor();
  return { context, page };
}
async function openPatient(page, id, name) {
  await page.goto(`${web}/patients/${id}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { level: 1, name }).waitFor();
}
const plansOf = page => page.getByRole('region', { name: 'Planuri de tratament' });
const docsOf = page => page.getByRole('region', { name: 'Documente și imagini' });
const paroOf = page => page.locator('.periodontal-section');
const respond = (page, method, test) => page.waitForResponse(r => r.request().method() === method && test(new URL(r.url()).pathname), { timeout: 10000 });
// Known pilot defect (SQLite runtime, pre-existing since T04): several APIs return naive ISO timestamps without an
// offset. They are stored as UTC; the expectation therefore reads them as UTC and the summary records each naive value.
const naiveTimestamps = [];
const asUtc = (iso, field) => { if (/(Z|[+-]\d\d:\d\d)$/.test(iso)) return iso; naiveTimestamps.push(field); return `${iso}Z`; };
const clinicFmt = (iso, tz, field = 'value') => new Intl.DateTimeFormat('ro-RO', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz }).format(new Date(asUtc(iso, field)));

const receptionApi = await apiSession(creds.reception, 'reception');
const doctorApi = await apiSession(creds.doctor, 'doctor');
let activePage = null;
try {
  // ---- Setup by reception through the real API: patient + appointment ----
  const clinic = (await doctorApi.call('GET', '/clinic')).body;
  summary.clinicTimezone = clinic?.timezone ?? null;
  check('clinic time zone known and different from the browser zone', Boolean(clinic?.timezone) && (clinic.timezone !== BROWSER_TZ || SAME_ZONE_RUN), { clinicTimezone: clinic?.timezone, browserTimezone: BROWSER_TZ });
  const tz = clinic.timezone;
  const me = (await doctorApi.call('GET', '/auth/me')).body;
  const rooms = (await receptionApi.call('GET', '/rooms')).body;
  const patientName = { first_name: 'Sorina', last_name: `Consolidat${tag}` };
  const patientCreate = await receptionApi.call('POST', '/patients', { ...patientName, phone: `07${tag.padStart(8, '0').slice(-8)}` });
  check('reception creates the fictional patient', patientCreate.status === 201, { status: patientCreate.status });
  const patient = patientCreate.body;
  const fullName = `${patient.first_name} ${patient.last_name}`;
  summary.ids.patient = patient.id;
  let visit = null;
  for (let attempt = 0; attempt < 25 && (!visit || visit.status === 409); attempt++) {
    const start = new Date(Date.now() + (2 + Math.floor(Math.random() * 120)) * 86400000);
    if ([0, 6].includes(start.getUTCDay())) continue;
    start.setUTCHours(7 + Math.floor(Math.random() * 6), Math.floor(Math.random() * 6) * 10, 0, 0);
    visit = await receptionApi.call('POST', '/appointments', { patient_id: patient.id, doctor_id: me.id, room_id: rooms[attempt % rooms.length].id, starts_at: start.toISOString(), duration_minutes: 45, appointment_type: 'Tratament consolidat fictiv' });
  }
  check('reception books the appointment', visit?.status === 201, { status: visit?.status, detail: visit?.body?.detail });
  const appointment = visit.body;
  summary.ids.appointment = appointment.id;
  const clinicTime = clinicFmt(appointment.starts_at, tz, 'appointment.starts_at');
  const utcTime = clinicFmt(appointment.starts_at, BROWSER_TZ);
  check('appointment clinic time differs from browser UTC time (observable zone)', clinicTime !== utcTime || SAME_ZONE_RUN, { clinicTime, utcTime });

  // ---- Doctor: odontogram finding in the UI ----
  const doctor = await login(creds.doctor, 'doctor');
  const page = doctor.page; activePage = page;
  await openPatient(page, patient.id, fullName);
  await page.locator('.patient-odontogram [data-tooth-id="16"]').waitFor();
  await plansOf(page).waitFor(); await docsOf(page).waitFor();
  await page.getByRole('heading', { name: 'Evaluare parodontală' }).waitFor();
  check('one record: one odontogram, one T08, one T09/T10 and one T11 panel', await page.locator('.patient-odontogram').count() === 1 && await plansOf(page).count() === 1
    && await paroOf(page).count() === 1 && await docsOf(page).count() === 1 && await page.locator('form.og-new-finding').count() === 1);
  await page.locator('.patient-odontogram [data-tooth-id="16"]').click();
  const findingText = `Carie ocluzală consolidat ${tag}`;
  const findingForm = page.locator('form.og-new-finding');
  await findingForm.getByLabel('Tipul constatării').selectOption({ label: 'Carie' });
  await findingForm.getByLabel('Descriere', { exact: true }).fill(findingText);
  await findingForm.locator('button.og-surface[aria-label="Ocluzală (occlusal)"]').click();
  const findingCreateP = respond(page, 'POST', p => /\/odontogram\/patients\/[^/]+\/findings$/.test(p));
  await findingForm.getByRole('button', { name: 'Salvează ciornă' }).click();
  const findingCreate = await findingCreateP;
  const finding = await findingCreate.json();
  check('odontogram finding draft saved on tooth 16', findingCreate.status() === 201 && finding.toothId === '16', { status: findingCreate.status() });
  const findingFinalizeP = respond(page, 'POST', p => p.endsWith(`/odontogram/findings/${finding.id}/finalize`));
  await page.locator(`.og-finding[data-finding-id="${finding.id}"]`).getByRole('button', { name: 'Finalizează explicit' }).click();
  check('odontogram finding finalized explicitly', (await findingFinalizeP).status() === 200);
  summary.ids.finding = finding.id;

  // ---- T07: procedure from the selected tooth; opening the record/tooth does not start time ----
  const catalog = (await doctorApi.call('GET', '/procedures?active=true&limit=50')).body;
  check('active catalog services available', catalog.length >= 2, { services: catalog.length });
  const [svc1, svc2] = catalog;
  await page.locator('.patient-odontogram [data-tooth-id="16"]').click();
  await page.getByRole('button', { name: 'Add performed procedure for this tooth' }).click();
  const entry = page.getByRole('region', { name: 'Add procedure for selected tooth' });
  await entry.getByLabel('Procedure visit').locator(`option[value="${appointment.id}"]`).waitFor({ state: 'attached' });
  await entry.getByLabel('Procedure visit').selectOption(appointment.id);
  const procForm = entry.locator('form.t07-procedure-form');
  await procForm.waitFor();
  await procForm.getByLabel('Procedure from active catalog').selectOption(svc1.id);
  check('tooth preselected from the odontogram', await procForm.getByLabel('Procedure tooth').inputValue() === '16');
  const procNote = `Obturație consolidat ${tag}`;
  await procForm.getByLabel('Procedure notes (optional)').fill(procNote);
  const procCreateP = respond(page, 'POST', p => p === '/api/performed-procedures');
  await procForm.getByRole('button', { name: 'Save procedure' }).click();
  const procCreate = await procCreateP;
  const procedure = await procCreate.json();
  check('T07 procedure saved pending from tooth (no timer started)', procCreate.status() === 201 && procedure.status === 'pending' && procedure.tooth_fdi === '16', { status: procCreate.status(), procedureStatus: procedure.status });
  const segmentsBefore = (await doctorApi.call('GET', `/performed-procedures/${procedure.id}/segments`)).body;
  check('no timer segment before explicit start', Array.isArray(segmentsBefore) && segmentsBefore.length === 0, { segments: segmentsBefore?.length });
  summary.ids.procedure = procedure.id;

  // ---- Visit page: explicit timer start/pause/resume/complete; visit stays open ----
  await page.goto(`${web}/appointments/${appointment.id}/visit`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: 'Performed procedures', exact: true }).waitFor();
  const card = page.locator('article.procedure-card').filter({ hasText: procNote });
  await card.waitFor();
  for (const [transition, label, expected] of [['start', 'Start', 'running'], ['pause', 'Pause', 'paused'], ['resume', 'Resume', 'running'], ['complete', 'Complete', 'completed']]) {
    const p = respond(page, 'POST', x => x.endsWith(`/performed-procedures/${procedure.id}/${transition}`));
    await card.getByRole('button', { name: label, exact: true }).click();
    const r = await p; const body = await r.json();
    check(`timer ${transition} -> ${expected}`, r.status() === 200 && body.status === expected, { status: r.status(), procedureStatus: body.status });
    if (transition === 'pause') await page.waitForTimeout(1100);
  }
  const segments = (await doctorApi.call('GET', `/performed-procedures/${procedure.id}/segments`)).body;
  const byType = segments.reduce((acc, s) => ({ ...acc, [s.segment_type]: (acc[s.segment_type] ?? 0) + 1 }), {});
  const span = s => (new Date(s.ended_at) - new Date(s.started_at)) / 1000;
  const pauseSeconds = segments.filter(s => s.segment_type === 'pause').reduce((sum, s) => sum + span(s), 0);
  const runSeconds = segments.filter(s => s.segment_type === 'run').reduce((sum, s) => sum + Math.trunc(span(s)), 0);
  const procAfter = (await doctorApi.call('GET', `/performed-procedures/${procedure.id}`)).body;
  check('timer persisted as closed segments: two run, one pause', byType.run === 2 && byType.pause === 1 && segments.every(s => s.ended_at), { byType });
  check('pause time is not counted in the active duration', pauseSeconds >= 1 && (procAfter.total_duration_seconds ?? 0) === runSeconds, { pauseSeconds, totalDurationSeconds: procAfter.total_duration_seconds, runSeconds });
  const apptAfterComplete = (await doctorApi.call('GET', `/appointments/${appointment.id}`)).body;
  check('completing the procedure does not close the visit', apptAfterComplete.status !== 'completed', { appointmentStatus: apptAfterComplete.status });
  await page.reload({ waitUntil: 'domcontentloaded' });
  await card.getByText('completed', { exact: true }).first().waitFor();
  check('visit still offers a separate close after procedure completion', await page.getByRole('button', { name: /Close visit|Mark confirmed/ }).count() > 0);
  await shot(page, 'visit-timer-completed-visit-open');

  // ---- T08: plan created in the UI and linked to the performed procedure ----
  await openPatient(page, patient.id, fullName);
  const plans = plansOf(page);
  await plans.getByRole('button', { name: 'Plan nou' }).click();
  const planTitle = `Plan consolidat ${tag}`;
  await page.getByLabel('Titlul planului').fill(planTitle);
  await page.getByLabel('Etapa 1 manopera 1 serviciu').selectOption(svc1.id);
  await page.getByLabel('Etapa 1 manopera 1 dinte').selectOption('16');
  await page.getByLabel('Etapa 2 manopera 1 serviciu').selectOption(svc2.id);
  await page.getByLabel('Etapa 2 manopera 1 dinte').selectOption('21');
  await page.getByRole('button', { name: 'Salvează planul' }).click();
  await plans.getByTestId('plan-status').waitFor();
  await page.getByLabel('Observație acceptare').fill('Acord verbal consemnat intern (fictiv)');
  await plans.getByRole('button', { name: 'Consemnează acceptarea versiunii 1' }).click();
  await plans.getByTestId('accepted-version').waitFor();
  const item1 = plans.getByRole('article', { name: `Manoperă planificată ${svc1.code} 16` });
  const performedSelect = item1.getByLabel(`Manoperă efectuată pentru ${svc1.code}`);
  await performedSelect.locator(`option[value="${procedure.id}"]`).waitFor({ state: 'attached' });
  await performedSelect.selectOption(procedure.id);
  await item1.getByRole('button', { name: 'Asociază manopera' }).click();
  await item1.getByTestId('item-progress').filter({ hasText: 'Efectuat' }).waitFor();
  const planList = (await doctorApi.call('GET', `/treatment-plans/patients/${patient.id}`)).body;
  const plan = (await doctorApi.call('GET', `/treatment-plans/${planList[0].id}`)).body;
  check('T08 plan item linked to the completed T07 procedure', planList.length === 1 && plan.stages[0].items[0].performed_procedure?.id === procedure.id && plan.stages[0].items[0].performed_procedure?.status === 'completed' && plan.status === 'in_progress',
    { plans: planList.length, planStatus: plan.status });
  summary.ids.plan = plan.id;
  await shot(page, 'plan-linked');

  // ---- T09/T10: finalized assessment, follow-up and comparison ----
  await openPatient(page, patient.id, fullName);
  const paro = paroOf(page);
  const expectedClinicDay = new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: tz }).format(new Date());
  check('default periodontal clinical date is the clinic day', await paro.getByLabel('Data clinică pentru o evaluare nouă').inputValue() === expectedClinicDay, { expectedClinicDay });
  await paro.getByRole('button', { name: 'Evaluare nouă' }).click();
  await page.locator('.periodontal-editor').waitFor();
  await page.getByLabel('Dinte FDI').selectOption('16');
  const site = page.locator('.periodontal-sites fieldset').first();
  await site.getByLabel('Adâncime (mm)').fill('4');
  await site.getByLabel('BOP').selectOption('yes');
  let finP = respond(page, 'POST', p => p.endsWith('/finalize'));
  await page.getByRole('button', { name: 'Salvează și finalizează' }).click();
  check('first periodontal assessment finalized', (await finP).status() === 200);
  await page.getByRole('button', { name: 'Creează reevaluare ulterioară' }).click();
  await page.getByLabel('Dinte FDI').selectOption('16');
  await page.locator('.periodontal-sites fieldset').first().getByLabel('Adâncime (mm)').fill('3');
  finP = respond(page, 'POST', p => p.endsWith('/finalize'));
  await page.getByRole('button', { name: 'Salvează și finalizează' }).click();
  check('follow-up periodontal assessment finalized', (await finP).status() === 200);
  const paroRows = (await doctorApi.call('GET', `/periodontal/patients/${patient.id}/assessments`)).body.filter(r => r.status === 'finalized');
  check('two finalized assessments persisted', paroRows.length === 2, { finalized: paroRows.length });
  summary.ids.assessments = paroRows.map(r => r.id);
  const shownId = await paro.getByLabel('Istoric').inputValue();
  const followUp = (await doctorApi.call('GET', `/periodontal/assessments/${shownId}`)).body;
  check('displayed assessment is a finalized one from this run', paroRows.some(r => r.id === shownId) && followUp.status === 'finalized', { shownIsFollowUp: shownId === paroRows[0].id });
  await paro.getByText(/Finalizată \d/).first().waitFor();
  const paroText = await paro.innerText();
  check('finalization time shown in the clinic zone, not raw UTC', paroText.includes(clinicFmt(followUp.finalized_at, tz, 'periodontal.finalized_at')) && !paroText.includes(followUp.finalized_at),
    { clinicTime: clinicFmt(followUp.finalized_at, tz), browserTime: clinicFmt(followUp.finalized_at, BROWSER_TZ) });
  await page.getByLabel('Prima evaluare').selectOption(paroRows[1].id);
  await page.getByLabel('A doua evaluare').selectOption(paroRows[0].id);
  await page.getByRole('button', { name: 'Compară' }).click();
  const comparison = page.locator('.periodontal-comparison-results');
  await comparison.getByText(/BOP a doua evaluare:/).first().waitFor();
  check('comparison shows the persisted delta', (await comparison.innerText()).includes('Δ -1'));
  await paro.screenshot({ path: path.join(evidenceDir, 'consolidated-periodontal-comparison.png') }); summary.artifacts.push('consolidated-periodontal-comparison.png');

  // ---- T11: document on the visit and tooth, zoom ----
  const docs = docsOf(page);
  await docs.getByLabel('Fișier').setInputFiles(rx.path);
  await docs.getByLabel('Categorie', { exact: true }).selectOption('radiograph');
  await docs.getByLabel('Vizită', { exact: true }).selectOption(appointment.id);
  await docs.getByLabel('Dinte', { exact: true }).selectOption('16');
  await docs.getByLabel('Descriere').fill('Radiografie control obturație (fictiv)');
  const upP = respond(page, 'POST', p => /\/api\/documents\/patients\/[^/]+$/.test(p));
  await docs.getByRole('button', { name: 'Salvează în dosar' }).click();
  const up = await upP;
  await docs.getByText('Fișier salvat în dosarul pacientului.').waitFor();
  const docRows = (await doctorApi.call('GET', `/documents/patients/${patient.id}`)).body;
  const doc = docRows.find(row => row.original_filename === rx.name) ?? {};
  check('document uploaded on visit + tooth', up.status() === 201 && doc.appointment?.id === appointment.id && doc.tooth_fdi === '16', { status: up.status() });
  summary.ids.document = doc.id;
  summary.documentSha256 = doc.sha256;
  const docItem = docs.locator('article.doc-item', { hasText: rx.name });
  await docItem.waitFor();
  const docText = await docItem.innerText();
  check('visit time on the document shown in the clinic zone', docText.includes(clinicTime) && (SAME_ZONE_RUN || !docText.includes(`, ${utcTime}`)), { clinicTime, utcTime });
  await docItem.getByRole('button', { name: 'Vizualizează' }).click();
  const viewer = docs.getByLabel('Vizualizare imagine');
  await page.waitForFunction(() => { const el = document.querySelector('.doc-viewer img'); return el && el.complete && el.naturalWidth > 0; });
  await viewer.getByRole('button', { name: 'Mărește' }).click();
  const zoomLevel = await viewer.getByTestId('zoom-level').innerText();
  const zoomedWidth = await viewer.locator('img').evaluate(el => el.getBoundingClientRect().width);
  check('zoom enlarges the authorized blob image', zoomLevel === '125%' && Math.abs(zoomedWidth - 1400 * 1.25) < 3 && (await viewer.locator('img').getAttribute('src')).startsWith('blob:'), { zoomLevel, zoomedWidth });
  await shot(page, 'document-zoom');

  // ---- Full reload: everything persists ----
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { level: 1, name: fullName }).waitFor();
  await page.getByText(findingText, { exact: true }).waitFor();
  await docsOf(page).locator('article.doc-item', { hasText: rx.name }).waitFor();
  await plansOf(page).getByRole('article', { name: `Rezumat ${planTitle}` }).waitFor();
  const historyOptions = await paroOf(page).getByLabel('Istoric').locator('option').evaluateAll(o => o.map(x => x.value).filter(Boolean));
  const recordText = await page.locator('.clinical-record').innerText();
  check('after full reload: finding, procedure, plan, assessments and document persist', recordText.includes(procedure.procedure_code) && recordText.includes('completed')
    && paroRows.every(r => historyOptions.includes(r.id)) && await page.locator('.patient-odontogram').count() === 1, { assessmentsInHistory: historyOptions.length });
  await shot(page, 'doctor-record-after-reload');

  // ---- Visit closed as a separate action ----
  await page.goto(`${web}/appointments/${appointment.id}/visit`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: 'Performed procedures', exact: true }).waitFor();
  for (const label of ['Mark confirmed', 'Mark arrived', 'Mark in progress']) {
    const p = respond(page, 'PATCH', x => x.endsWith(`/appointments/${appointment.id}/status`));
    await page.getByRole('button', { name: label, exact: true }).click();
    await p;
  }
  const closeP = respond(page, 'PATCH', x => x.endsWith(`/appointments/${appointment.id}/status`));
  await page.getByRole('button', { name: 'Close visit', exact: true }).click();
  const closed = await (await closeP).json();
  check('visit closed only by the separate close action', closed.status === 'completed', { appointmentStatus: closed.status });
  await doctor.context.close();

  // ---- Manager on the same record ----
  const manager = await login(creds.manager, 'manager'); activePage = manager.page;
  await openPatient(manager.page, patient.id, fullName);
  await docsOf(manager.page).locator('article.doc-item', { hasText: rx.name }).waitFor();
  await manager.page.getByText(findingText, { exact: true }).waitFor();
  await plansOf(manager.page).getByRole('article', { name: `Rezumat ${planTitle}` }).waitFor();
  const managerHistory = await paroOf(manager.page).getByLabel('Istoric').locator('option').evaluateAll(o => o.map(x => x.value).filter(Boolean));
  check('manager sees odontogram, plan, finalized assessments and document', paroRows.every(r => managerHistory.includes(r.id)) && await manager.page.locator('.patient-odontogram').count() === 1);
  await shot(manager.page, 'manager-record');

  // ---- Reception on the same record: no clinical modules, API refused ----
  const reception = await login(creds.reception, 'reception'); activePage = reception.page;
  await openPatient(reception.page, patient.id, fullName);
  await reception.page.waitForTimeout(800);
  const receptionVisible = { odontogram: await reception.page.locator('.patient-odontogram').count(), plans: await plansOf(reception.page).count(),
    periodontal: await paroOf(reception.page).count(), documents: await docsOf(reception.page).count() };
  check('reception UI shows no odontogram, plan, periodontal or document panel', Object.values(receptionVisible).every(v => v === 0), receptionVisible);
  const rDenied = {
    odontogram: (await receptionApi.call('GET', `/odontogram/patients/${patient.id}`)).status,
    plans: (await receptionApi.call('GET', `/treatment-plans/patients/${patient.id}`)).status,
    periodontal: (await receptionApi.call('GET', `/periodontal/patients/${patient.id}/assessments`)).status,
    documents: (await receptionApi.call('GET', `/documents/patients/${patient.id}`)).status,
    documentContent: (await receptionApi.call('GET', `/documents/${doc.id}/content`, undefined, true)).status,
  };
  check('reception API refused (403) on all clinical modules', Object.values(rDenied).every(s => s === 403), rDenied);
  await shot(reception.page, 'reception-record');

  // ---- Second clinic: nothing visible ----
  const otherCtx = await request.newContext();
  const otherEmail = `consolidat-other-${tag}-${Math.random().toString(16).slice(2, 8)}@example.com`;
  const otherPassword = `fictiv-${Math.random().toString(36).slice(2)}-${Date.now()}`;
  const reg = await otherCtx.post(`${api}/auth/register`, { data: { clinic_name: `Clinică izolare fictivă ${tag}`, full_name: 'Manager altă clinică fictiv', email: otherEmail, password: otherPassword } });
  check('second fictional clinic registered', reg.status() === 201, { status: reg.status() });
  const other = sessionFromToken(otherCtx, (await reg.json()).access_token);
  const oDenied = {
    patient: (await other.call('GET', `/patients/${patient.id}`)).status,
    odontogram: (await other.call('GET', `/odontogram/patients/${patient.id}`)).status,
    plan: (await other.call('GET', `/treatment-plans/${plan.id}`)).status,
    assessment: (await other.call('GET', `/periodontal/assessments/${paroRows[0].id}`)).status,
    assessments: (await other.call('GET', `/periodontal/patients/${patient.id}/assessments`)).status,
    documents: (await other.call('GET', `/documents/patients/${patient.id}`)).status,
    document: (await other.call('GET', `/documents/${doc.id}`)).status,
    documentContent: (await other.call('GET', `/documents/${doc.id}/content`, undefined, true)).status,
  };
  check('second clinic gets 404 for patient, odontogram, plan, assessment and document', Object.values(oDenied).every(s => s === 404), oDenied);
  const otherList = (await other.call('GET', `/patients?search=${encodeURIComponent(patient.last_name)}`)).body;
  check('second clinic patient search does not return the patient', Array.isArray(otherList) ? !otherList.some(p => p.id === patient.id) : !JSON.stringify(otherList ?? '').includes(patient.id));
  const otherUi = await login({ email: otherEmail, password: otherPassword }, 'other-clinic'); activePage = otherUi.page;
  await otherUi.page.goto(`${web}/patients/${patient.id}`, { waitUntil: 'domcontentloaded' });
  await otherUi.page.waitForTimeout(1500);
  const otherText = await otherUi.page.locator('body').innerText();
  check('second clinic UI does not show the patient, the document or the assessment', !otherText.includes(patient.last_name) && !otherText.includes(rx.name)
    && await docsOf(otherUi.page).locator('article.doc-item').count() === 0 && await paroOf(otherUi.page).locator('.periodontal-history').count() === 0);
  await shot(otherUi.page, 'other-clinic-refused');
  await otherCtx.dispose();

  check('no browser page errors', summary.pageErrors.length === 0, { pageErrors: summary.pageErrors });
  summary.result = 'PASS';
} catch (error) {
  summary.result = 'FAIL';
  summary.errors.push(String(error.message ?? error).slice(0, 1200));
  if (activePage) await shot(activePage, 'failure').catch(() => {});
  process.exitCode = 1;
} finally {
  for (const context of contexts) await context.close().catch(() => {});
  await receptionApi.ctx.dispose();
  await doctorApi.ctx.dispose();
  await browser.close();
  const passed = summary.checks.filter(c => c.passed).length;
  summary.totals = { passed, total: summary.checks.length };
  summary.knownDefectNaiveApiTimestamps = [...new Set(naiveTimestamps)];
  await fs.writeFile(path.join(evidenceDir, 'consolidated-summary.json'), JSON.stringify(summary, null, 2), { mode: 0o600 });
  console.log(`Consolidated browser ${summary.result}: ${passed}/${summary.checks.length} checks · page errors ${summary.pageErrors.length} · console/http errors ${summary.consoleErrors.length} · evidence ${evidenceDir}`);
  if (summary.errors.length) console.log(summary.errors.join('\n'));
}
