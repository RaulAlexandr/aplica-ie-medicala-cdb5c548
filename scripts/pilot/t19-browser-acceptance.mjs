// T19 acceptance through the real UI, as manager, doctor and reception, on a demo started by demo.sh.
// It adds data (shifts, leave, attendance, a document): run it on its own demo database.
// Credentials come only from the local 600 file named by PILOT_CREDENTIALS and never reach the summary.
// Every HTTP >= 400 seen by a page must be listed as expected; anything else fails the run.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const required = ['DENTACARE_PILOT_HOME', 'DENTACARE_APP_SHA', 'PILOT_WEB_URL', 'PILOT_API_URL', 'PILOT_CREDENTIALS', 'PLAYWRIGHT_MODULE', 'CHROMIUM_EXECUTABLE'];
for (const key of required) if (!process.env[key]) throw new Error(`Missing ${key}`);
const { chromium, request } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const creds = JSON.parse(await fs.readFile(process.env.PILOT_CREDENTIALS, 'utf8'));
const web = process.env.PILOT_WEB_URL.replace(/\/$/, '');
const api = process.env.PILOT_API_URL.replace(/\/$/, '');
const TZ = 'Europe/Bucharest';
const evidenceDir = process.env.T19_EVIDENCE_DIR || path.join(process.env.DENTACARE_PILOT_HOME, 'browser-evidence', 't19');
await fs.mkdir(evidenceDir, { recursive: true, mode: 0o700 });
const summary = { task: 'T19-team', appSha: process.env.DENTACARE_APP_SHA, browser: null, browserTimezone: TZ, checks: [], artifacts: [], pageErrors: [], unexpectedHttp: [], expectedHttp: [], errors: [] };
const check = (name, ok, details = {}) => { summary.checks.push({ name, passed: Boolean(ok), ...details }); if (!ok) throw new Error(`T19 failed: ${name} ${JSON.stringify(details)}`); };
const idPath = url => { try { return new URL(url).pathname.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id'); } catch { return null; } };
const expected = [];
const expectHttp = (method, pattern, status) => expected.push({ method, pattern, status, used: false });
const shot = async (page, name) => { const file = path.join(evidenceDir, `t19-${name}.png`); await page.screenshot({ path: file, fullPage: true }); summary.artifacts.push(path.basename(file)); };
const clinicDay = date => new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit' }).format(date);
const addDays = (day, count) => { const value = new Date(`${day}T12:00:00Z`); value.setUTCDate(value.getUTCDate() + count); return value.toISOString().slice(0, 10); };
const mondayAfter = (day, weeks) => addDays(day, 7 * weeks - ((new Date(`${day}T12:00:00Z`).getUTCDay() + 6) % 7));
const clinicLocal = date => { const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date).map(p => [p.type, p.value])); return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`; };

async function apiSession(account) {
  const ctx = await request.newContext();
  const login = await ctx.post(`${api}/auth/login`, { data: { email: account.email, password: account.password } });
  if (!login.ok()) throw new Error(`API login failed: ${login.status()}`);
  const token = (await login.json()).access_token;
  const call = async (method, url, data) => { const response = await ctx.fetch(`${api}${url}`, { method, data, headers: { Authorization: `Bearer ${token}` } }); return { status: response.status(), body: await response.json().catch(() => null) }; };
  return { ctx, call };
}

const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
summary.browser = browser.version();
async function login(account, label) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 950 }, timezoneId: TZ, locale: 'ro-RO', acceptDownloads: true });
  const page = await context.newPage();
  page.on('pageerror', error => summary.pageErrors.push({ user: label, message: String(error.message).slice(0, 300) }));
  page.on('response', response => {
    if (response.status() < 400) return;
    const method = response.request().method(); const route = idPath(response.url());
    const match = expected.find(e => !e.used && e.method === method && e.status === response.status() && e.pattern.test(route));
    if (match) { match.used = true; summary.expectedHttp.push({ user: label, method, route, status: response.status() }); }
    else summary.unexpectedHttp.push({ user: label, method, route, status: response.status() });
  });
  await page.goto(web, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill(account.email);
  await page.getByLabel('Password').fill(account.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Echipă', exact: true }).click();
  await page.getByRole('heading', { name: 'Echipă', level: 1 }).waitFor();
  return page;
}
const region = (page, name) => page.getByRole('region', { name, exact: true });
const respond = (page, method, test) => page.waitForResponse(r => r.request().method() === method && test(new URL(r.url()).pathname));
async function showDay(page, day) {
  const schedule = region(page, 'Cine lucrează unde și când');
  await schedule.getByRole('button', { name: 'Zi', exact: true }).click();
  await schedule.getByLabel('Data').fill(day);
  await schedule.locator(`[data-day="${day}"]`).waitFor();
  return schedule.locator(`[data-day="${day}"]`);
}

const managerApi = await apiSession(creds.manager);
const doctorApi = await apiSession(creds.doctor);
const receptionApi = await apiSession(creds.reception);
try {
  const members = (await managerApi.call('GET', '/team/members')).body;
  const doctor = members.find(m => m.email === creds.doctor.email);
  const receptionist = members.find(m => m.email === creds.reception.email);
  check('seeded doctor and reception are team members', Boolean(doctor && receptionist));
  const room = (await managerApi.call('GET', '/rooms')).body.find(r => r.is_active);
  const monday = mondayAfter(clinicDay(new Date()), 3);
  const tuesday = addDays(monday, 1);

  // ===== Manager: document, shifts, overlap refusal =====
  const manager = await login(creds.manager, 'manager');
  check('manager sees assignment and team attendance', await region(manager, 'Repartizare ture').isVisible() && await region(manager, 'Prezența echipei').isVisible());
  const docs = region(manager, 'Documente administrative');
  await docs.getByLabel('Persoană').selectOption(doctor.id);
  await docs.getByLabel('Fișier (PDF, JPEG, PNG)').setInputFiles({ name: 'contract-fictiv.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n') });
  const uploaded = respond(manager, 'POST', p => /\/api\/team\/members\/[^/]+\/documents$/.test(p));
  await docs.getByRole('button', { name: 'Încarcă documentul' }).click();
  const uploadResponse = await uploaded;
  check('manager uploads a protected document for the doctor (201)', uploadResponse.status() === 201);
  const documentId = (await uploadResponse.json()).id;
  await docs.getByText('contract-fictiv.pdf').waitFor();
  check('the file field is empty after upload', await docs.getByLabel('Fișier (PDF, JPEG, PNG)').evaluate(el => el.files.length === 0));

  const shifts = region(manager, 'Repartizare ture');
  const addShift = async (personId, roomId, start, end) => {
    await shifts.getByLabel('Persoană').selectOption(personId);
    if (roomId) await shifts.getByLabel('Cabinet').selectOption(roomId);
    await shifts.getByLabel('Început').fill(start);
    await shifts.getByLabel('Sfârșit').fill(end);
    const done = respond(manager, 'POST', p => p === '/api/team/shifts');
    await shifts.getByRole('button', { name: 'Adaugă tura' }).click();
    return (await done).status();
  };
  check('manager assigns the doctor to a room (201)', await addShift(doctor.id, room.id, `${monday}T09:00`, `${monday}T13:00`) === 201);
  expectHttp('POST', /^\/api\/team\/shifts$/, 409);
  check('an overlapping shift for the same person is refused (409)', await addShift(doctor.id, room.id, `${monday}T12:00`, `${monday}T15:00`) === 409);
  await shifts.getByText('Persoana are deja o tură în acest interval').waitFor();
  check('reception is assigned without a room (201)', await addShift(receptionist.id, null, `${monday}T08:00`, `${monday}T16:00`) === 201);
  const managerDay = await showDay(manager, monday);
  await managerDay.getByText(doctor.full_name).waitFor();
  const managerDayText = await managerDay.innerText();
  check('the day view shows who, where and when in clinic time', managerDayText.includes('09:00–13:00') && managerDayText.includes(room.name) && managerDayText.includes('08:00–16:00'), { day: monday });
  await shot(manager, 'manager-schedule');

  // ===== Doctor: own shift, double-click clock-in, leave, own document =====
  const doctorPage = await login(creds.doctor, 'doctor');
  check('doctor has no manager sections', await region(doctorPage, 'Repartizare ture').count() === 0 && await region(doctorPage, 'Prezența echipei').count() === 0);
  const ownDay = await showDay(doctorPage, monday);
  await ownDay.getByText('(tu)').first().waitFor();
  check('doctor sees the assigned shift', (await ownDay.innerText()).includes(room.name));
  const attendance = region(doctorPage, 'Prezența mea');
  await attendance.getByRole('button', { name: 'Înregistrează intrarea' }).dblclick();
  await attendance.getByRole('button', { name: 'Înregistrează ieșirea' }).waitFor();
  const afterDouble = (await doctorApi.call('GET', '/team/attendance/me')).body;
  check('double click on clock-in leaves one open session', Boolean(afterDouble.open) && afterDouble.sessions.filter(s => s.is_open).length === 1 && afterDouble.sessions.length === 1);
  const clockedOut = respond(doctorPage, 'POST', p => p === '/api/team/attendance/clock-out');
  await attendance.getByRole('button', { name: 'Înregistrează ieșirea' }).click();
  check('doctor clocks out (200)', (await clockedOut).status() === 200);
  await attendance.getByRole('button', { name: 'Înregistrează intrarea' }).waitFor();

  const leavePanel = region(doctorPage, 'Concedii');
  await leavePanel.getByLabel('Tip concediu').selectOption('medical');
  await leavePanel.getByLabel('Prima zi').fill(tuesday);
  await leavePanel.getByLabel('Ultima zi').fill(tuesday);
  await leavePanel.getByLabel('Motiv (opțional)').fill('Motiv privat fictiv');
  const requested = respond(doctorPage, 'POST', p => p === '/api/team/leave');
  await leavePanel.getByRole('button', { name: 'Trimite cererea' }).click();
  const leaveResponse = await requested;
  check('doctor requests leave (201)', leaveResponse.status() === 201);
  const leaveId = (await leaveResponse.json()).id;
  await leavePanel.getByText('În așteptare').waitFor();
  const ownDocs = region(doctorPage, 'Documente administrative');
  await ownDocs.getByText('contract-fictiv.pdf').waitFor();
  const downloadEvent = doctorPage.waitForEvent('download');
  await ownDocs.getByRole('button', { name: 'Descarcă' }).click();
  const file = await downloadEvent;
  check('doctor downloads the own document', file.suggestedFilename() === 'contract-fictiv.pdf');
  check('doctor cannot upload documents', await ownDocs.getByRole('button', { name: 'Încarcă documentul' }).count() === 0);
  await shot(doctorPage, 'doctor-team');

  // ===== Reception: own shift, attendance, refused access to another person's documents =====
  const receptionPage = await login(creds.reception, 'reception');
  check('reception has no manager sections', await region(receptionPage, 'Repartizare ture').count() === 0);
  const receptionDay = await showDay(receptionPage, monday);
  await receptionDay.getByText('(tu)').first().waitFor();
  const receptionAttendance = region(receptionPage, 'Prezența mea');
  await receptionAttendance.getByRole('button', { name: 'Înregistrează intrarea' }).click();
  await receptionAttendance.getByRole('button', { name: 'Înregistrează ieșirea' }).click();
  await receptionAttendance.getByRole('button', { name: 'Înregistrează intrarea' }).waitFor();
  const receptionDocs = region(receptionPage, 'Documente administrative');
  await receptionDocs.getByText('Niciun document.').waitFor();
  check('reception UI does not list the doctor document', !(await receptionPage.locator('main.content').innerText()).includes('contract-fictiv.pdf'));
  const inBrowser = await receptionPage.evaluate(async ({ apiUrl, doctorId, doc }) => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('access_token')}` };
    const list = await fetch(`${apiUrl}/team/members/${doctorId}/documents`, { headers });
    const content = await fetch(`${apiUrl}/team/documents/${doc}/content`, { headers });
    const missing = await fetch(`${apiUrl}/team/documents/00000000-0000-4000-8000-000000000000/content`, { headers });
    return { list: list.status, content: content.status, missing: missing.status, same: (await content.text()) === (await missing.text()) };
  }, { apiUrl: api, doctorId: doctor.id, doc: documentId }).catch(error => ({ error: String(error) }));
  summary.unexpectedHttp = summary.unexpectedHttp.filter(item => !(item.user === 'reception' && /\/api\/team\/(members\/:id\/documents|documents\/:id\/content)$/.test(item.route) && [403, 404].includes(item.status)));
  check('reception is refused the doctor document list (403)', inBrowser.list === 403, inBrowser);
  check('reception gets the same 404 for a real and a missing document', inBrowser.content === 404 && inBrowser.missing === 404 && inBrowser.same, inBrowser);
  const leaveSeen = await receptionApi.call('GET', '/team/leave');
  check('reception does not see the doctor leave request', leaveSeen.status === 200 && !leaveSeen.body.some(l => l.id === leaveId));
  check('reception cannot approve leave (403)', (await receptionApi.call('POST', `/team/leave/${leaveId}/approve`)).status === 403);
  check('reception cannot create shifts (403)', (await receptionApi.call('POST', '/team/shifts', { user_id: receptionist.id, room_id: null, local_start: `${tuesday}T09:00`, local_end: `${tuesday}T10:00` })).status === 403);
  await shot(receptionPage, 'reception-team');

  // ===== Manager: approve leave into the agenda, correct attendance with a reason =====
  await manager.reload({ waitUntil: 'domcontentloaded' });
  const managerLeave = region(manager, 'Concedii');
  const row = managerLeave.locator('article', { hasText: doctor.full_name }).filter({ hasText: 'În așteptare' });
  await row.waitFor();
  check('manager sees the leave type', (await row.innerText()).includes('Medical'));
  const approved = respond(manager, 'POST', p => p.endsWith('/approve'));
  await row.getByRole('button', { name: 'Aprobă' }).click();
  check('manager approves the leave (200)', (await approved).status() === 200);
  await managerLeave.getByText('Concediu aprobat.').waitFor();
  const blocks = (await receptionApi.call('GET', '/availability/unavailability')).body.filter(b => b.resource_id === doctor.id);
  check('approval creates one block in the existing agenda source', blocks.length === 1 && blocks[0].reason === 'Concediu');
  const patient = (await managerApi.call('GET', '/patients?limit=1')).body[0];
  const refused = await managerApi.call('POST', '/appointments', { patient_id: patient.id, doctor_id: doctor.id, room_id: room.id, local_start: `${tuesday}T10:00:00`, duration_minutes: 30, appointment_type: 'Control' });
  check('the agenda refuses a booking during approved leave (422)', refused.status === 422, { status: refused.status });
  const colleagueView = await receptionApi.call('GET', `/team/schedule?day=${tuesday}`);
  check('colleagues see the absence without type or reason', colleagueView.body.leaves.length === 1 && !JSON.stringify(colleagueView.body).includes('Motiv privat') && !JSON.stringify(colleagueView.body).includes('medical'));

  const teamAttendance = region(manager, 'Prezența echipei');
  const sessionRow = teamAttendance.locator('article', { hasText: doctor.full_name }).first();
  await sessionRow.waitFor();
  await sessionRow.getByRole('button', { name: 'Corectează' }).click();
  const correctedIn = clinicLocal(new Date(Date.now() - 90 * 60000));
  const form = teamAttendance.getByRole('form', { name: 'Corecție prezență' });
  await form.getByLabel('Intrare corectă').fill(correctedIn);
  await form.getByLabel('Motivul corecției').fill('A uitat să pontească (fictiv)');
  const corrected = respond(manager, 'POST', p => p.endsWith('/corrections'));
  await form.getByRole('button', { name: 'Salvează corecția' }).click();
  const correctionResponse = await corrected;
  check('manager saves a correction (201)', correctionResponse.status() === 201);
  const body = await correctionResponse.json();
  check('the original clock-in is kept and the correction is a new record', body.clock_in_at === afterDouble.open.clock_in_at && body.effective_clock_in_at !== body.clock_in_at && body.corrections.length === 1);
  await teamAttendance.getByText('A uitat să pontească (fictiv)').waitFor();
  await shot(manager, 'manager-correction');

  // ===== DST in the view =====
  const spring = await showDay(manager, '2027-03-28');
  check('the spring change day is labelled 23 hours', (await spring.innerText()).includes('zi de 23 ore'));
  const autumn = await showDay(manager, '2027-10-31');
  check('the autumn change day is labelled 25 hours', (await autumn.innerText()).includes('zi de 25 ore'));

  check('no page errors', summary.pageErrors.length === 0, { pageErrors: summary.pageErrors });
  check('no unexpected HTTP errors in the browser', summary.unexpectedHttp.length === 0, { unexpected: summary.unexpectedHttp });
  summary.result = 'PASS';
} catch (error) {
  summary.result = 'FAIL';
  summary.errors.push(String(error.message).slice(0, 800));
} finally {
  await browser.close();
  for (const session of [managerApi, doctorApi, receptionApi]) await session.ctx.dispose();
  await fs.writeFile(path.join(evidenceDir, 'summary.json'), JSON.stringify(summary, null, 2), { mode: 0o600 });
  console.log(JSON.stringify({ result: summary.result, checks: summary.checks.length, failed: summary.checks.filter(c => !c.passed).map(c => c.name), errors: summary.errors, unexpectedHttp: summary.unexpectedHttp, pageErrors: summary.pageErrors, artifacts: summary.artifacts }, null, 1));
}
process.exit(summary.result === 'PASS' ? 0 : 1);
