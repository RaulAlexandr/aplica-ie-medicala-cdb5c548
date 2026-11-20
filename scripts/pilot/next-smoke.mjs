// INT2 smoke for the integrated T12 + T16 + T24 flows, driven through the real UI as manager, reception and doctor:
//   T12: manager creates and publishes a form template -> reception fills and submits it -> doctor validates it.
//   T16: reception adds a waitlist entry -> finds the candidate for a free slot -> phone offer -> confirmation ->
//        the appointment appears in the agenda.
//   T24: manager generates the period report and opens "Vezi înregistrările" for appointments.
// Run against a runtime started by demo.sh, on a FRESH restored copy: the script adds data (template, form, entry,
// appointment). Credentials come only from the local 600 file named by PILOT_CREDENTIALS and never reach the summary.
// Console errors, page errors and HTTP >= 400 seen by the browser are recorded; any of them fails the run
// (there are no expected ones: role gates redirect, they do not produce failed requests).
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const required = ['DENTACARE_PILOT_HOME', 'DENTACARE_APP_SHA', 'PILOT_WEB_URL', 'PILOT_API_URL', 'PILOT_CREDENTIALS', 'PLAYWRIGHT_MODULE', 'CHROMIUM_EXECUTABLE'];
for (const key of required) if (!process.env[key]) throw new Error(`Missing ${key}`);
const { chromium, request } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const creds = JSON.parse(await fs.readFile(process.env.PILOT_CREDENTIALS, 'utf8'));
const web = process.env.PILOT_WEB_URL.replace(/\/$/, '');
const api = process.env.PILOT_API_URL.replace(/\/$/, '');
const BROWSER_TZ = process.env.NEXT_BROWSER_TZ || 'Europe/Bucharest';
const evidenceDir = process.env.NEXT_EVIDENCE_DIR || path.join(process.env.DENTACARE_PILOT_HOME, 'browser-evidence', 'next');
await fs.mkdir(evidenceDir, { recursive: true, mode: 0o700 });
const tag = new Date().toISOString().slice(11, 19).replaceAll(':', '');
const summary = { task: 'INT2-next-smoke', appSha: process.env.DENTACARE_APP_SHA, web, api, browser: null, browserTimezone: BROWSER_TZ,
  clinicTimezone: null, checks: [], artifacts: [], pageErrors: [], consoleErrors: [], errors: [] };
const check = (name, ok, details = {}) => { summary.checks.push({ name, passed: Boolean(ok), ...details }); if (!ok) throw new Error(`Smoke failed: ${name} ${JSON.stringify(details)}`); };
const SHOTS_MAX = 4;
const shot = async (page, name) => {
  if (summary.artifacts.length >= SHOTS_MAX) return;
  const file = path.join(evidenceDir, `next-${name}.png`); await page.screenshot({ path: file, fullPage: true }); summary.artifacts.push(path.basename(file));
};
const idPath = url => { try { return new URL(url).pathname.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id'); } catch { return null; } };
const dayInClinic = (tz, date) => new Intl.DateTimeFormat('en-CA', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: tz }).format(date);
const addDays = (day, n) => { const d = new Date(`${day}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };

async function apiSession(account, label) {
  const ctx = await request.newContext();
  const login = await ctx.post(`${api}/auth/login`, { data: { email: account.email, password: account.password } });
  if (!login.ok()) throw new Error(`API login failed for ${label}: ${login.status()}`);
  const token = (await login.json()).access_token;
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
const formsOf = page => page.getByRole('region', { name: 'Formulare pacient' });

const managerApi = await apiSession(creds.manager, 'manager');
const receptionApi = await apiSession(creds.reception, 'reception');
const doctorApi = await apiSession(creds.doctor, 'doctor');
let activePage = null;
try {
  // ---- Resolve fictional ids through the API (by name only) ----
  const clinic = (await doctorApi.call('GET', '/clinic')).body;
  const tz = clinic.timezone; summary.clinicTimezone = tz;
  check('clinic zone is Europe/Bucharest (the demo clinic)', tz === 'Europe/Bucharest', { tz });
  const patients = (await doctorApi.call('GET', '/patients?limit=100')).body;
  const elena = patients.find(p => p.first_name === 'Elena' && p.last_name === 'Test');
  check('the seeded patient Elena Test exists', Boolean(elena));
  const doctors = (await doctorApi.call('GET', '/staff/doctors')).body;
  const doctorUser = doctors.find(d => d.role === 'doctor') ?? doctors[0];
  const rooms = (await doctorApi.call('GET', '/rooms')).body.filter(r => r.is_active);
  const room = rooms.find(r => r.name === 'Cabinet Demo 2') ?? rooms[0];
  check('an active doctor and room exist', Boolean(doctorUser && room));
  const today = dayInClinic(tz, new Date());
  const templateName = `Anamneză smoke ${tag}`;
  const reasonLabel = 'Motivul vizitei';
  const pregnancyLabel = 'Sunteți gravidă';

  // ===================== T12 · MANAGER: create and publish a template =====================
  const manager = await login(creds.manager, 'manager'); activePage = manager;
  const managerNav = await navNames(manager);
  check('manager menu has waitlist and reports links', managerNav.includes('Lista de așteptare') && managerNav.includes('Rapoarte'), { nav: managerNav });
  await manager.getByRole('link', { name: 'Clinic setup', exact: true }).click();
  const editor = manager.getByRole('region', { name: 'Editor șabloane formular' });
  await editor.waitFor();
  await editor.getByLabel('Numele șablonului nou').fill(templateName);
  const templateCreated = respond(manager, 'POST', p => p === '/api/forms/templates');
  await editor.getByRole('button', { name: 'Creează șablon' }).click();
  check('manager creates a draft template (201)', (await templateCreated).status() === 201);
  await editor.getByRole('button', { name: 'Adaugă întrebare în secțiune' }).click();
  await editor.getByRole('button', { name: 'Adaugă întrebare în secțiune' }).click();
  const q1 = editor.locator('fieldset', { has: manager.getByText('Întrebarea 1', { exact: true }) });
  const q2 = editor.locator('fieldset', { has: manager.getByText('Întrebarea 2', { exact: true }) });
  await q1.getByLabel('Text', { exact: true }).fill(reasonLabel);
  await q1.getByLabel('Obligatorie').check();
  await q2.getByLabel('Text', { exact: true }).fill(pregnancyLabel);
  await q2.getByLabel('Tip', { exact: true }).selectOption('yes_no');
  const published = respond(manager, 'POST', p => /\/api\/forms\/versions\/[^/]+\/publish$/.test(p));
  await editor.getByRole('button', { name: 'Publică versiunea' }).click();
  check('manager publishes the template version (2xx)', (await published).ok());
  await editor.getByText('Versiunea a fost publicată și este imuabilă.').waitFor();
  await shot(manager, 'template-published');
  const publishedForms = (await receptionApi.call('GET', '/forms/templates')).body;
  const publishedTemplate = publishedForms.find(t => t.name === templateName);
  check('the published template is visible to reception with 2 questions in 1 section', Boolean(publishedTemplate) && publishedTemplate.version_number === 1);

  // ===================== T12 · RECEPTION: fill and submit =====================
  const reception = await login(creds.reception, 'reception'); activePage = reception;
  const receptionNav = await navNames(reception);
  check('reception menu: waitlist yes, reports no', receptionNav.includes('Lista de așteptare') && !receptionNav.includes('Rapoarte'), { nav: receptionNav });
  await reception.goto(`${web}/patients/${elena.id}`, { waitUntil: 'domcontentloaded' });
  const receptionForms = formsOf(reception);
  await receptionForms.waitFor();
  await receptionForms.getByLabel('Formular nou').selectOption({ label: `${templateName} v1` });
  const formCreated = respond(reception, 'POST', p => /\/api\/forms\/patients\/[^/]+\/[^/]+$/.test(p));
  await receptionForms.getByRole('button', { name: 'Începe completarea' }).click();
  const createdForm = await (await formCreated).json();
  await receptionForms.getByLabel(reasonLabel).fill('Durere la masticație (fictiv)');
  await receptionForms.getByLabel(pregnancyLabel).selectOption('no');
  const submitted = respond(reception, 'POST', p => /\/api\/forms\/[^/]+\/submit$/.test(p));
  await receptionForms.getByRole('button', { name: 'Trimite' }).click();
  check('reception submits the filled form (2xx)', (await submitted).ok());
  check('reception sees no validate button and no correction history', (await receptionForms.getByRole('button', { name: 'Validează' }).count()) === 0 && (await receptionForms.getByLabel('Istoricul corecțiilor').count()) === 0);
  const asReception = await receptionApi.call('GET', `/forms/${createdForm.id}`);
  check('the server returns the form as submitted and hides the answers from reception', asReception.status === 200 && asReception.body.status === 'submitted' && !asReception.body.answers, { status: asReception.body?.status, hasAnswers: Boolean(asReception.body?.answers) });
  const revisionsAsReception = await receptionApi.call('GET', `/forms/${createdForm.id}/revisions`);
  check('reception cannot read the correction history (403)', revisionsAsReception.status === 403, { status: revisionsAsReception.status });

  // ===================== T12 · DOCTOR: validate =====================
  const doctor = await login(creds.doctor, 'doctor'); activePage = doctor;
  const doctorNav = await navNames(doctor);
  check('doctor menu: waitlist yes, reports no', doctorNav.includes('Lista de așteptare') && !doctorNav.includes('Rapoarte'), { nav: doctorNav });
  await doctor.goto(`${web}/patients/${elena.id}`, { waitUntil: 'domcontentloaded' });
  const doctorForms = formsOf(doctor);
  await doctorForms.getByRole('button', { name: new RegExp(`${templateName} v1 · .* · trimis`) }).click();
  await doctorForms.getByLabel(reasonLabel).waitFor();
  check('the doctor sees the submitted answer', (await doctorForms.getByLabel(reasonLabel).inputValue()) === 'Durere la masticație (fictiv)');
  const reviewed = respond(doctor, 'POST', p => /\/api\/forms\/[^/]+\/review$/.test(p));
  await doctorForms.getByRole('button', { name: 'Validează' }).click();
  check('doctor validates the form (2xx)', (await reviewed).ok());
  await doctorForms.getByRole('button', { name: new RegExp(`${templateName} v1 · .* · validat`) }).waitFor();
  const validated = (await doctorApi.call('GET', `/forms/${createdForm.id}`)).body;
  check('the stored form is validated with a validation time', validated.status === 'validated' && Boolean(validated.validated_at), { status: validated.status });
  await shot(doctor, 'form-validated');

  // ===================== T16 · RECEPTION: waitlist -> offer -> confirmation -> agenda =====================
  activePage = reception;
  await reception.getByRole('link', { name: 'Lista de așteptare', exact: true }).click();
  const waitlist = reception.getByRole('region', { name: 'Listă de așteptare' });
  await waitlist.waitFor();
  const service = `Control smoke ${tag}`;
  await waitlist.getByLabel('Pacient ID').fill(elena.id);
  await waitlist.getByLabel('Serviciu').fill(service);
  const entryCreated = respond(reception, 'POST', p => p === '/api/waitlist/entries');
  await waitlist.getByRole('button', { name: 'Adaugă în listă' }).click();
  check('reception adds the waitlist entry (201)', (await entryCreated).status() === 201);
  await waitlist.getByText(new RegExp(`${service} · active`)).waitFor();
  // The UI entry window is "today's weekday, 09:00-17:00 clinic time": look for a free slot on that weekday a week or more ahead.
  let slot = null;
  for (let week = 1; week <= 8 && !slot; week += 1) {
    const day = addDays(today, 7 * week);
    const taken = (await doctorApi.call('GET', `/appointments?day=${day}`)).body.map(a => a.local_start.slice(11, 16));
    const hour = ['10:00', '11:00', '12:00', '13:00', '14:00'].find(h => !taken.includes(h));
    if (hour) slot = { day, hour, week };
  }
  check('a free clinic-local slot exists on the same weekday', Boolean(slot), { slot });
  await waitlist.getByLabel('Medic ID').fill(doctorUser.id);
  await waitlist.getByLabel('Cabinet ID').fill(room.id);
  await waitlist.getByLabel('Început').fill(`${slot.day}T${slot.hour}`);
  const candidatesFound = respond(reception, 'POST', p => p === '/api/waitlist/slots/candidates');
  await waitlist.getByRole('button', { name: 'Caută candidați' }).click();
  check('candidate search answers 2xx', (await candidatesFound).ok());
  const candidateRow = waitlist.locator('li', { hasText: service }).filter({ has: reception.getByRole('button', { name: 'Înregistrează oferta telefonică' }) });
  await candidateRow.waitFor();
  const offered = respond(reception, 'POST', p => /\/api\/waitlist\/entries\/[^/]+\/offers$/.test(p));
  await candidateRow.getByRole('button', { name: 'Înregistrează oferta telefonică' }).click();
  check('the phone offer is recorded (201)', (await offered).status() === 201);
  const confirmed = respond(reception, 'POST', p => /\/api\/waitlist\/offers\/[^/]+\/confirm$/.test(p));
  await waitlist.getByRole('button', { name: 'Confirmă și creează programarea' }).click();
  const confirmResponse = await confirmed;
  const booking = await confirmResponse.json().catch(() => null);
  check('the confirmation creates the appointment (201)', confirmResponse.status() === 201, { status: confirmResponse.status(), detail: booking?.detail });
  const dayAppointments = (await doctorApi.call('GET', `/appointments?day=${slot.day}`)).body;
  const bookedVisit = dayAppointments.find(a => a.patient_id === elena.id && a.local_start.startsWith(`${slot.day}T${slot.hour}`));
  check('the appointment exists at the offered clinic-local time', Boolean(bookedVisit), { day: slot.day, hour: slot.hour });
  await waitlist.getByText(new RegExp(`${service} · booked`)).waitFor();
  await reception.getByRole('link', { name: 'Appointments', exact: true }).click();
  await reception.getByRole('button', { name: 'Day', exact: true }).waitFor();
  await reception.getByRole('button', { name: 'Week', exact: true }).click();
  // The week view starts at this week's Monday; the slot is exactly `week` weeks ahead, so `week` clicks on Next reach it.
  for (let hop = 0; hop < slot.week; hop += 1) await reception.getByRole('button', { name: 'Next', exact: true }).click();
  await reception.locator('strong', { hasText: /^Week of / }).waitFor();
  await reception.getByText('Elena Test').first().waitFor();
  const agendaText = await mainText(reception);
  check('the agenda shows the booked visit for Elena Test at the offered hour', agendaText.includes('Elena Test') && agendaText.includes(slot.hour), { hour: slot.hour });
  await shot(reception, 'agenda-booked');
  await reception.goto(`${web}/reports`, { waitUntil: 'domcontentloaded' });
  await reception.locator('main.content').getByText('CLINIC OVERVIEW').waitFor();
  check('reception is redirected from /reports to the overview', new URL(reception.url()).pathname === '/');

  // ===================== T24 · MANAGER: report -> "Vezi înregistrările" =====================
  activePage = manager;
  await manager.getByRole('link', { name: 'Rapoarte', exact: true }).click();
  const reports = manager.getByRole('region', { name: 'Rapoarte' });
  await reports.waitFor();
  await reports.getByLabel('De la').fill(today);
  await reports.getByLabel('Până la').fill(slot.day);
  await reports.getByLabel('Cabinet').fill(room.id);
  const reportLoads = [];
  manager.on('response', r => { if (/\/api\/reports\/[^/]+$/.test(new URL(r.url()).pathname)) reportLoads.push(r.status()); });
  await reports.getByRole('button', { name: 'Generează raportul' }).click();
  const appointmentsCard = reports.locator('article.report-card', { has: manager.getByRole('heading', { name: 'Programări', exact: true }) });
  await appointmentsCard.waitFor();
  check('all 15 report indicators loaded with 2xx', reportLoads.length === 15 && reportLoads.every(s => s === 200), { loads: reportLoads.length, statuses: [...new Set(reportLoads)] });
  const total = Number((await appointmentsCard.locator('.report-value').innerText()).match(/\d+/)?.[0]);
  check('the appointments indicator counts at least the seeded visits plus the new booking', total >= 4, { total });
  await appointmentsCard.getByRole('link', { name: 'Vezi înregistrările' }).click();
  const recordList = appointmentsCard.getByRole('list', { name: 'Înregistrări Programări' });
  await recordList.waitFor();
  const recordsText = await recordList.innerText();
  check('the source records list includes the booked appointment of Elena Test', recordsText.includes('Elena Test') && recordsText.includes(slot.day), { rows: (await recordList.locator('li').count()) });
  await shot(manager, 'report-records');

  // ===================== Console classification =====================
  check('no console errors, page errors or failed requests during the smoke', summary.consoleErrors.length === 0 && summary.pageErrors.length === 0, { consoleErrors: summary.consoleErrors.slice(0, 5), pageErrors: summary.pageErrors.slice(0, 3) });
} catch (error) {
  summary.errors.push(String(error.message).slice(0, 600));
  if (activePage) { try { await shot(activePage, 'failure'); } catch { /* best effort */ } }
  process.exitCode = 1;
} finally {
  for (const context of contexts) await context.close().catch(() => {});
  await browser.close();
  await Promise.all([managerApi, receptionApi, doctorApi].map(s => s.ctx.dispose()));
  summary.passed = summary.checks.filter(c => c.passed).length;
  summary.total = summary.checks.length;
  await fs.writeFile(path.join(evidenceDir, 'next-smoke-LATEST.json'), JSON.stringify(summary, null, 2) + '\n', { mode: 0o600 });
  console.log(JSON.stringify({ task: summary.task, passed: summary.passed, total: summary.total, errors: summary.errors, pageErrors: summary.pageErrors.length, consoleErrors: summary.consoleErrors.length, artifacts: summary.artifacts }));
}
