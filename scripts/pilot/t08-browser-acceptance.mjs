// T08 browser acceptance against a running fictional pilot runtime (real API + SQLite).
// T08 actions go through the UI. Appointment and performed-procedure execution are
// prepared through the existing T02/T04 HTTP API, because T08 deliberately has no
// execution path. Credentials are read locally and never written to the summary.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const required = ['DENTACARE_PILOT_HOME', 'DENTACARE_APP_SHA', 'PILOT_WEB_URL', 'PILOT_API_URL', 'PILOT_CREDENTIALS', 'PLAYWRIGHT_MODULE', 'CHROMIUM_EXECUTABLE'];
for (const key of required) if (!process.env[key]) throw new Error(`Missing ${key}`);
const { chromium, request } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const creds = JSON.parse(await fs.readFile(process.env.PILOT_CREDENTIALS, 'utf8'));
const web = process.env.PILOT_WEB_URL.replace(/\/$/, '');
const api = process.env.PILOT_API_URL.replace(/\/$/, '');
const evidenceDir = path.join(process.env.DENTACARE_PILOT_HOME, 'browser-evidence');
await fs.mkdir(evidenceDir, { recursive: true, mode: 0o700 });
const runId = new Date().toISOString().replaceAll(':', '-');
const summary = { task: 'T08', appSha: process.env.DENTACARE_APP_SHA, web, api, browser: null, checks: [], artifacts: [], network: {}, errors: [] };
const check = (name, ok, details = {}) => { summary.checks.push({ name, passed: Boolean(ok), ...details }); if (!ok) throw new Error(`Acceptance failed: ${name} ${JSON.stringify(details)}`); };
const shot = async (page, name) => { const file = path.join(evidenceDir, `${runId}-t08-${name}.png`); await page.screenshot({ path: file, fullPage: true }); summary.artifacts.push(file); };

async function apiSession(account) {
  const ctx = await request.newContext();
  const login = await ctx.post(`${api}/auth/login`, { data: { email: account.email, password: account.password } });
  if (!login.ok()) throw new Error(`API login failed for ${account.email}: ${login.status()}`);
  const token = (await login.json()).access_token;
  const call = async (method, url, data) => {
    const response = await ctx.fetch(`${api}${url}`, { method, data, headers: { Authorization: `Bearer ${token}` } });
    const body = await response.json().catch(() => null);
    return { status: response.status(), body };
  };
  return { ctx, call };
}

const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
summary.browser = browser.version();
const posts = { planCreate: 0, accept: 0, appointmentLink: 0, performedLink: 0 };
const pageErrors = [];
async function login(account) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(String(error.message).slice(0, 300)));
  page.on('request', req => {
    const url = new URL(req.url()).pathname;
    if (req.method() === 'POST' && /\/treatment-plans\/patients\/[^/]+$/.test(url)) posts.planCreate += 1;
    if (req.method() === 'POST' && /\/treatment-plans\/[^/]+\/accept$/.test(url)) posts.accept += 1;
    if (req.method() === 'PUT' && /\/items\/[^/]+\/appointment$/.test(url)) posts.appointmentLink += 1;
    if (req.method() === 'PUT' && /\/items\/[^/]+\/performed-procedure$/.test(url)) posts.performedLink += 1;
  });
  await page.goto(web, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill(account.email);
  await page.getByLabel('Password').fill(account.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Patients', exact: true }).waitFor();
  return { context, page };
}
async function openPatient(page, id) {
  await page.goto(`${web}/patients/${id}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { level: 1 }).waitFor();
  await page.getByRole('region', { name: 'Planuri de tratament' }).waitFor();
}
async function openPlan(page, title) {
  const panel = page.getByRole('region', { name: 'Planuri de tratament' });
  await panel.getByRole('article', { name: `Rezumat ${title}` }).getByRole('button', { name: 'Deschide planul' }).click();
  await panel.getByTestId('plan-status').waitFor();
  return panel;
}
const itemCard = (panel, code, tooth) => panel.getByRole('article', { name: `Manoperă planificată ${code} ${tooth}` });

const doctorApi = await apiSession(creds.doctor);
let doctor = null;
try {
  // ---- Setup with fictional data (T00-T04 existing APIs) ----
  const me = (await doctorApi.call('GET', '/auth/me')).body;
  const rooms = (await doctorApi.call('GET', '/rooms')).body;
  const catalog = (await doctorApi.call('GET', '/procedures?active=true&limit=50')).body;
  check('setup: two active catalog services', catalog.length >= 2, { services: catalog.length });
  const [svc1, svc2] = catalog;
  const patientA = (await doctorApi.call('POST', '/patients', { first_name: 'Ioana', last_name: `T08 Fictiv ${runId.slice(11, 19)}` })).body;
  const patientB = (await doctorApi.call('POST', '/patients', { first_name: 'Victor', last_name: `T08 Altul ${runId.slice(11, 19)}` })).body;
  const start = new Date(Date.now() + 3 * 86400000); start.setUTCHours(9, Math.floor(Math.random() * 6) * 10, 0, 0);
  const visit = await doctorApi.call('POST', '/appointments', { patient_id: patientA.id, doctor_id: me.id, room_id: rooms[0].id, starts_at: start.toISOString(), duration_minutes: 30, appointment_type: 'Tratament T08 fictiv' });
  check('setup: fictional appointment created via T02 API', visit.status === 201, { status: visit.status, detail: visit.body?.detail });
  const executionCounts = async () => {
    const procedures = (await doctorApi.call('GET', `/performed-procedures?patient_id=${patientA.id}&limit=200`)).body;
    let segments = 0;
    for (const row of procedures) segments += (await doctorApi.call('GET', `/performed-procedures/${row.id}/segments`)).body.length;
    return { procedures: procedures.length, segments };
  };
  const before = await executionCounts();

  // ---- Doctor creates the plan in the browser ----
  doctor = await login(creds.doctor);
  const { page } = doctor;
  await openPatient(page, patientA.id);
  const panel = page.getByRole('region', { name: 'Planuri de tratament' });
  await panel.getByText('Pacientul nu are planuri de tratament.').waitFor();
  check('patient record shows treatment plans entry point', await panel.getByRole('button', { name: 'Plan nou' }).isEnabled());
  check('single odontogram instance kept', await page.locator('.patient-odontogram').count() === 1);
  await panel.getByRole('button', { name: 'Plan nou' }).click();
  const title = `Plan reabilitare fictiv ${runId.slice(11, 19)}`;
  await page.getByLabel('Titlul planului').fill(title);
  await page.getByLabel('Observații plan').fill('Plan demonstrativ cu date fictive');
  await page.getByLabel('Etapa 1 manopera 1 serviciu').selectOption(svc1.id);
  await page.getByLabel('Etapa 1 manopera 1 dinte').selectOption('16');
  await page.getByRole('group', { name: 'Etapa 1 manopera 1 suprafețe' }).getByLabel('Ocluzală').check();
  await page.getByLabel('Etapa 1 manopera 1 prioritate').selectOption('urgent');
  await page.getByLabel('Etapa 2 manopera 1 serviciu').selectOption(svc2.id);
  await page.getByLabel('Etapa 2 manopera 1 dinte').selectOption('21');
  await page.getByRole('group', { name: 'Etapa 2 manopera 1 suprafețe' }).getByLabel('Mezială').check();
  await page.getByLabel('Etapa 2 manopera 1 tarif estimat').fill('410.50');

  // Server error once: the draft must stay intact.
  let failed = false;
  await page.route('**/api/treatment-plans/patients/*', async route => {
    if (!failed && route.request().method() === 'POST') { failed = true; return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Serviciu indisponibil (simulat)' }) }); }
    return route.continue();
  });
  await page.getByRole('button', { name: 'Salvează planul' }).click();
  await page.getByText('Serviciu indisponibil (simulat)').waitFor();
  check('error keeps the form draft', await page.getByLabel('Titlul planului').inputValue() === title
    && await page.getByLabel('Etapa 2 manopera 1 tarif estimat').inputValue() === '410.50'
    && await page.getByRole('group', { name: 'Etapa 1 manopera 1 suprafețe' }).getByLabel('Ocluzală').isChecked());
  await shot(page, 'form-error-keeps-draft');
  await page.unroute('**/api/treatment-plans/patients/*');
  const createsBefore = posts.planCreate;
  await page.getByRole('button', { name: 'Salvează planul' }).dblclick();
  await panel.getByTestId('plan-status').waitFor();
  const plans = (await doctorApi.call('GET', `/treatment-plans/patients/${patientA.id}`)).body;
  check('repeated save creates exactly one plan', plans.length === 1, { plans: plans.length, createRequestsAfterError: posts.planCreate - createsBefore });
  const expectedTotal = (Number(svc1.base_price) * 100 + 41050) / 100;
  const shownTotal = await panel.getByTestId(`estimated-${svc1.currency}`).innerText();
  check('estimates computed server-side and labelled', shownTotal === `${expectedTotal.toFixed(2)} ${svc1.currency}` && await panel.getByText(/Estimare; nu este factură/).isVisible(), { shownTotal });
  check('two ordered stages with two locations', await panel.getByRole('region', { name: 'Etapa 1' }).isVisible() && await panel.getByRole('region', { name: 'Etapa 2' }).isVisible()
    && await itemCard(panel, svc1.code, '16').isVisible() && await itemCard(panel, svc2.code, '21').isVisible());
  check('plan creation leaves execution untouched', JSON.stringify(await executionCounts()) === JSON.stringify(before), before);
  await shot(page, 'plan-created-draft');

  // ---- Explicit acceptance (double click guarded) ----
  await page.getByLabel('Observație acceptare').fill('Acord exprimat verbal în cabinet după explicarea etapelor (fictiv)');
  await panel.getByRole('button', { name: 'Consemnează acceptarea versiunii 1' }).dblclick();
  await panel.getByTestId('accepted-version').waitFor();
  check('acceptance recorded once with actor and version', posts.accept === 1 && (await panel.getByTestId('accepted-version').innerText()).includes('Versiunea 1 acceptată'), { acceptRequests: posts.accept });
  check('acceptance leaves execution untouched', JSON.stringify(await executionCounts()) === JSON.stringify(before));
  await shot(page, 'plan-accepted');

  // ---- Associate the visit ----
  const card1 = itemCard(panel, svc1.code, '16');
  await card1.getByLabel(`Vizită pentru ${svc1.code}`).selectOption(visit.body.id);
  await card1.getByRole('button', { name: 'Asociază vizita' }).dblclick();
  await card1.getByTestId('item-progress').filter({ hasText: 'Programat' }).waitFor();
  check('visit linked once', posts.appointmentLink === 1, { requests: posts.appointmentLink });

  // ---- T04 executes a compatible procedure (existing flow, via API) ----
  const performed = await doctorApi.call('POST', '/performed-procedures', { appointment_id: visit.body.id, procedure_catalog_id: svc1.id, tooth_fdi: '16', tooth_surface: 'occlusal' });
  const wrong = await doctorApi.call('POST', '/performed-procedures', { appointment_id: visit.body.id, procedure_catalog_id: svc1.id, tooth_fdi: '17', tooth_surface: 'occlusal' });
  check('setup: T04 procedures created outside T08', performed.status === 201 && wrong.status === 201);
  await page.reload({ waitUntil: 'domcontentloaded' });
  let detail = await openPlan(page, title);
  const options = await itemCard(detail, svc1.code, '16').getByLabel(`Manoperă efectuată pentru ${svc1.code}`).locator('option').evaluateAll(nodes => nodes.map(node => node.value).filter(Boolean));
  check('only the compatible performed procedure is offered', options.length === 1 && options[0] === performed.body.id, { options: options.length });
  const firstItemId = (await doctorApi.call('GET', `/treatment-plans/${plans[0].id}`)).body.stages[0].items[0].id;
  const incompatible = await doctorApi.call('PUT', `/treatment-plans/items/${firstItemId}/performed-procedure`, { performed_procedure_id: wrong.body.id });
  check('server rejects incompatible location', incompatible.status === 422, { status: incompatible.status });
  await itemCard(detail, svc1.code, '16').getByLabel(`Manoperă efectuată pentru ${svc1.code}`).selectOption(performed.body.id);
  await itemCard(detail, svc1.code, '16').getByRole('button', { name: 'Asociază manopera' }).dblclick();
  await itemCard(detail, svc1.code, '16').getByTestId('item-progress').filter({ hasText: 'În execuție' }).waitFor();
  check('pending execution is not done; plan in progress', (await detail.getByTestId('plan-status').innerText()) === 'În lucru' && posts.performedLink === 1, { requests: posts.performedLink });

  const started = await doctorApi.call('POST', `/performed-procedures/${performed.body.id}/start`);
  const completed = await doctorApi.call('POST', `/performed-procedures/${performed.body.id}/complete`);
  check('setup: T04 execution completed through existing timer API', started.status === 200 && completed.status === 200);
  await page.reload({ waitUntil: 'domcontentloaded' });
  detail = await openPlan(page, title);
  await itemCard(detail, svc1.code, '16').getByTestId('item-progress').filter({ hasText: 'Efectuat' }).waitFor();
  check('progress recalculated after completion', (await detail.getByTestId('plan-progress').innerText()).startsWith('1 efectuate')
    && await detail.getByRole('button', { name: 'Finalizează planul' }).isDisabled()
    && (await detail.getByTestId('completion-blocker').innerText()).includes('neither completed'));
  await shot(page, 'plan-progress-50');

  // ---- Resolve the remaining item and complete ----
  const card2 = itemCard(detail, svc2.code, '21');
  await card2.getByLabel(`Motiv pentru ${svc2.code}`).fill('Pacientul a amânat restaurarea (fictiv)');
  await card2.getByRole('button', { name: 'Anulează manopera planificată' }).click();
  await card2.getByTestId('item-progress').filter({ hasText: 'Anulat explicit' }).waitFor();
  await detail.getByRole('button', { name: 'Finalizează planul' }).click();
  await detail.getByTestId('plan-status').filter({ hasText: 'Finalizat' }).waitFor();
  await shot(page, 'plan-completed');

  // ---- Reload persistence ----
  await page.reload({ waitUntil: 'domcontentloaded' });
  detail = await openPlan(page, title);
  const persisted = {
    status: await detail.getByTestId('plan-status').innerText(),
    progress: await detail.getByTestId('plan-progress').innerText(),
    acceptance: await detail.getByTestId('acceptance-row').count(),
    linkShown: await itemCard(detail, svc1.code, '16').getByText(/Manoperă efectuată:/).isVisible(),
    visitShown: await itemCard(detail, svc1.code, '16').getByText(/Vizită:/).isVisible(),
  };
  check('state persists after reload', persisted.status === 'Finalizat' && persisted.acceptance === 1 && persisted.linkShown && persisted.visitShown, persisted);
  await shot(page, 'plan-after-reload');

  // ---- Patient switch: no form or plan carried over ----
  await page.getByRole('button', { name: 'Înapoi la planuri' }).click();
  await panel.getByRole('button', { name: 'Plan nou' }).click();
  await page.getByLabel('Titlul planului').fill('Ciornă care nu trebuie transferată');
  await page.getByRole('link', { name: 'Patients', exact: true }).click();
  await page.locator(`a[href="/patients/${patientB.id}"]`).click();
  await page.getByRole('region', { name: 'Planuri de tratament' }).getByText('Pacientul nu are planuri de tratament.').waitFor();
  check('patient switch does not carry the form or plans', await page.getByLabel('Titlul planului').count() === 0 && await page.getByText(title).count() === 0);
  await shot(page, 'patient-b-clean');

  // ---- Server-side invariants after the run ----
  const final = (await doctorApi.call('GET', `/treatment-plans/${plans[0].id}`)).body;
  const after = await executionCounts();
  check('only T04 created execution records (2 procedures, 1 timer run)', after.procedures === before.procedures + 2 && after.segments >= 1, { before, after });
  check('history records actor/time for links and acceptance', ['created', 'accepted', 'appointment_linked', 'performed_linked', 'item_cancelled', 'completed'].every(action => final.history.some(event => event.action === action && event.actor)));

  // ---- Reception has no T08 access ----
  const reception = await apiSession(creds.reception);
  const denied = await reception.call('GET', `/treatment-plans/patients/${patientA.id}`);
  check('reception denied by API', denied.status === 403, { status: denied.status });
  const receptionBrowser = await login(creds.reception);
  await receptionBrowser.page.goto(`${web}/patients/${patientA.id}`, { waitUntil: 'domcontentloaded' });
  await receptionBrowser.page.getByRole('heading', { level: 1 }).waitFor();
  check('reception UI shows no treatment plan panel', await receptionBrowser.page.getByRole('region', { name: 'Planuri de tratament' }).count() === 0);
  await receptionBrowser.context.close();
  await reception.ctx.dispose();
  check('no uncaught page errors', pageErrors.length === 0, { pageErrors });
} catch (error) {
  summary.errors.push(String(error?.message ?? error).slice(0, 1500));
  if (doctor) await shot(doctor.page, 'failure').catch(() => {});
} finally {
  summary.network = posts;
  await doctorApi.ctx.dispose();
  await browser.close();
  const file = path.join(evidenceDir, `${runId}-t08-summary.json`);
  await fs.writeFile(file, JSON.stringify(summary, null, 2), { mode: 0o600 });
  console.log(JSON.stringify({ ...summary, summaryFile: file }, null, 2));
  if (summary.errors.length) process.exitCode = 1;
}
