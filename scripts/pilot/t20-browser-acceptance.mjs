// T20 acceptance through the real UI, as manager, doctor, assistant and reception, on a demo started by demo.sh.
// It adds data (an invited assistant, shifts, messages, tasks, a handover): run it on its own demo database.
// Credentials come only from the local 600 file named by PILOT_CREDENTIALS and never reach the summary.
// Every HTTP >= 400 seen by a page must be listed as expected; anything else fails the run.
import { randomBytes } from 'node:crypto';
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
const evidenceDir = process.env.T20_EVIDENCE_DIR || path.join(process.env.DENTACARE_PILOT_HOME, 'browser-evidence', 't20');
await fs.mkdir(evidenceDir, { recursive: true, mode: 0o700 });
const summary = { task: 'T20-chat-tasks', appSha: process.env.DENTACARE_APP_SHA, browser: null, browserTimezone: TZ, checks: [], artifacts: [], pageErrors: [], unexpectedHttp: [], expectedHttp: [], errors: [] };
const check = (name, ok, details = {}) => { summary.checks.push({ name, passed: Boolean(ok), ...details }); if (!ok) throw new Error(`T20 failed: ${name} ${JSON.stringify(details)}`); };
const idPath = url => { try { return new URL(url).pathname.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id'); } catch { return null; } };
const expected = [];
const expectHttp = (method, pattern, status) => expected.push({ method, pattern, status, used: false });
const shot = async (page, name) => { const file = path.join(evidenceDir, `t20-${name}.png`); await page.screenshot({ path: file, fullPage: true }); summary.artifacts.push(path.basename(file)); };
const clinicLocal = date => { const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date).map(p => [p.type, p.value])); return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`; };
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

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
const MENU = /^Mesaje și sarcini/;
async function login(account, label) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 950 }, timezoneId: TZ, locale: 'ro-RO' });
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
  await page.getByRole('link', { name: MENU }).click();
  await page.getByRole('heading', { name: 'Mesaje și sarcini', level: 1 }).waitFor();
  return page;
}
const region = (page, name) => page.getByRole('region', { name, exact: true });
const respond = (page, method, test) => page.waitForResponse(r => r.request().method() === method && test(new URL(r.url()).pathname));
const tab = (page, name) => page.getByRole('group', { name: 'Secțiuni' }).getByRole('button', { name }).click();
const badge = page => page.locator('nav .collab-badge');
const composer = scope => scope.getByRole('form', { name: 'Scrie un mesaj' });
const openConversation = async (page, title) => { await region(page, 'Conversații').getByRole('button', { name: title }).first().click(); await region(page, 'Conversație').getByRole('heading', { name: title }).waitFor(); return region(page, 'Conversație'); };
const taskRow = (page, title) => region(page, 'Sarcini').locator(`article[aria-label="Sarcină: ${title}"]`);
const taskStatus = (row, label) => row.locator('p.muted').filter({ hasText: 'Responsabil:' }).filter({ hasText: label }).waitFor();
async function applyTask(page, title, status, reason) {
  const row = taskRow(page, title);
  await row.getByLabel('Status').selectOption(status);
  if (reason) await row.getByLabel('Motiv (obligatoriu)').fill(reason);
  const done = respond(page, 'POST', p => p.endsWith('/changes'));
  await row.getByRole('button', { name: 'Aplică' }).click();
  return (await done).status();
}

const managerApi = await apiSession(creds.manager);
const doctorApi = await apiSession(creds.doctor);
const receptionApi = await apiSession(creds.reception);
let assistantApi = null;
try {
  // ===== Setup through the existing flows: invited assistant, one room with shifts, one without =====
  const assistantAccount = { email: `asistent-t20-${randomBytes(4).toString('hex')}@example.com`, password: randomBytes(18).toString('base64url') };
  const invited = await managerApi.call('POST', '/staff/invitations', { email: assistantAccount.email, full_name: 'Ana Asistentă Demo', role: 'assistant' });
  check('the manager invites a fictional assistant', invited.status === 201, { status: invited.status });
  const token = new URL(invited.body.invitation_url, web).searchParams.get('token');
  const accepted = await (await request.newContext()).post(`${api}/staff/invitations/accept?token=${encodeURIComponent(token)}`, { data: { password: assistantAccount.password } });
  check('the assistant activates the account', accepted.status() === 200, { status: accepted.status() });
  assistantApi = await apiSession(assistantAccount);
  const members = (await managerApi.call('GET', '/team/members')).body;
  const doctor = members.find(m => m.email === creds.doctor.email);
  const assistant = members.find(m => m.email === assistantAccount.email);
  const receptionist = members.find(m => m.email === creds.reception.email);
  const room = (await managerApi.call('GET', '/rooms')).body.find(r => r.is_active);
  const emptyRoom = (await managerApi.call('POST', '/rooms', { name: 'Cabinet fără tură T20' })).body;
  const start = clinicLocal(new Date(Date.now() - 60 * 60000)); const end = clinicLocal(new Date(Date.now() + 180 * 60000));
  for (const person of [doctor, assistant]) {
    const shift = await managerApi.call('POST', '/team/shifts', { user_id: person.id, room_id: room.id, local_start: start, local_end: end });
    check(`active shift in the room for ${person.role}`, shift.status === 201, { status: shift.status });
  }

  const manager = await login(creds.manager, 'manager');
  const doctorPage = await login(creds.doctor, 'doctor');
  const assistantPage = await login(assistantAccount, 'assistant');
  const receptionPage = await login(creds.reception, 'reception');

  // ===== Reception: message to a room with a shift, and to a room without one =====
  const toRoom = region(receptionPage, 'Mesaj către cabinet');
  await toRoom.getByLabel('Cabinet').selectOption(room.id);
  check('the composer shows the medical data notice', (await toRoom.innerText()).includes('Nu scrie date medicale în chat; folosește dosarul pacientului'));
  await composer(toRoom).locator('textarea').fill('Pacientul fictiv a sosit la cabinet');
  const roomSent = respond(receptionPage, 'POST', p => p.endsWith(`/rooms/${room.id}/messages`));
  await composer(toRoom).getByRole('button', { name: 'Trimite' }).click();
  const roomResponse = await roomSent;
  const roomBody = await roomResponse.json();
  check('the room message reaches the team assigned there (201)', roomResponse.status() === 201 && roomBody.recipients.includes(doctor.full_name) && roomBody.recipients.includes(assistant.full_name) && roomBody.recipients.length === 2, { status: roomResponse.status() });
  await toRoom.getByText('Trimis către:').waitFor();

  await toRoom.getByLabel('Cabinet').selectOption(emptyRoom.id);
  expectHttp('POST', /^\/api\/collab\/rooms\/:id\/messages$/, 409);
  await composer(toRoom).locator('textarea').fill('Mesaj către cabinet gol');
  const emptySent = respond(receptionPage, 'POST', p => p.endsWith(`/rooms/${emptyRoom.id}/messages`));
  await composer(toRoom).getByRole('button', { name: 'Trimite' }).click();
  check('a room without a shift is refused (409)', (await emptySent).status() === 409);
  await toRoom.getByText('Nimeni nu este repartizat acum').waitFor();
  check('the refused text stays in the composer', await composer(toRoom).locator('textarea').inputValue() === 'Mesaj către cabinet gol');
  await shot(receptionPage, 'reception-room-refused');
  await toRoom.getByLabel('Cabinet').selectOption(room.id);
  check('choosing another room keeps the written text', await composer(toRoom).locator('textarea').inputValue() === 'Mesaj către cabinet gol');
  await composer(toRoom).locator('textarea').fill('');

  // ===== Doctor and assistant: unread through polling, then read =====
  await badge(doctorPage).waitFor({ timeout: 20000 });
  check('polling brings the unread badge to the doctor', (await badge(doctorPage).innerText()) === '1');
  await badge(assistantPage).waitFor({ timeout: 20000 });
  check('polling brings the unread badge to the assistant', (await badge(assistantPage).innerText()) === '1');
  check('the manager, not assigned, has no unread badge', await badge(manager).count() === 0);
  await region(doctorPage, 'Conversații').getByRole('button', { name: room.name }).waitFor({ timeout: 20000 });
  const doctorThread = await openConversation(doctorPage, room.name);
  await doctorThread.getByText('Pacientul fictiv a sosit la cabinet').waitFor();
  await badge(doctorPage).waitFor({ state: 'detached', timeout: 10000 });
  check('opening the conversation clears the unread badge', true);
  await composer(doctorThread).locator('textarea').fill('Vin imediat');
  const replied = respond(doctorPage, 'POST', p => p.endsWith('/messages'));
  await composer(doctorThread).getByRole('button', { name: 'Trimite' }).click();
  const replyBody = await (await replied).json();
  check('the reply goes to the people assigned now, without the author', replyBody.recipients.length === 1 && replyBody.recipients[0] === assistant.full_name);
  await doctorThread.getByText('Vin imediat').waitFor();
  check('the author has no unread for the own message', await badge(doctorPage).count() === 0);
  await shot(doctorPage, 'doctor-room-conversation');

  // ===== Direct message: double click, then refused access for a non-member =====
  await region(doctorPage, 'Conversație nouă').getByLabel('Mesaj direct către').selectOption(receptionist.id);
  await region(doctorPage, 'Conversație nouă').getByRole('button', { name: 'Deschide' }).click();
  const directThread = region(doctorPage, 'Conversație');
  await directThread.getByRole('heading', { name: receptionist.full_name }).waitFor();
  await composer(directThread).locator('textarea').fill('Mesaj direct fictiv');
  await composer(directThread).getByRole('button', { name: 'Trimite' }).dblclick();
  await directThread.getByText('Mesaj direct fictiv').waitFor();
  const conversations = (await doctorApi.call('GET', '/collab/conversations')).body;
  const directItem = conversations.find(c => c.kind === 'direct');
  const directMessages = (await doctorApi.call('GET', `/collab/conversations/${directItem.id}/messages`)).body.messages;
  check('a double click stores one message', directMessages.length === 1);
  const again = (await receptionApi.call('POST', '/collab/direct', { user_id: doctor.id })).body;
  check('the same pair gets the same direct conversation', again.id === directItem.id);
  expectHttp('GET', /^\/api\/collab\/conversations\/:id\/messages$/, 404);
  expectHttp('GET', /^\/api\/collab\/conversations\/:id\/messages$/, 404);
  const refused = await assistantPage.evaluate(async ({ apiUrl, id }) => {
    const headers = { Authorization: `Bearer ${localStorage.getItem('access_token')}` };
    const real = await fetch(`${apiUrl}/collab/conversations/${id}/messages`, { headers });
    const missing = await fetch(`${apiUrl}/collab/conversations/00000000-0000-4000-8000-000000000000/messages`, { headers });
    return { real: real.status, missing: missing.status, same: (await real.text()) === (await missing.text()) };
  }, { apiUrl: api, id: directItem.id });
  check('another person gets the same 404 for a foreign and a missing conversation', refused.real === 404 && refused.missing === 404 && refused.same, refused);
  check('the foreign conversation is not listed', !(await region(assistantPage, 'Conversații').innerText()).includes('Mesaj direct fictiv'));

  // ===== Group: creator adds a member =====
  await region(doctorPage, 'Conversație nouă').getByLabel('Nume').fill('Tură fictivă');
  await region(doctorPage, 'Conversație nouă').getByRole('group', { name: 'Membri' }).getByLabel(assistant.full_name).check();
  await region(doctorPage, 'Conversație nouă').getByRole('button', { name: 'Creează' }).click();
  const groupThread = region(doctorPage, 'Conversație');
  await groupThread.getByRole('heading', { name: 'Tură fictivă' }).waitFor();
  await groupThread.getByLabel('Adaugă membru').selectOption(receptionist.id);
  const memberAdded = respond(doctorPage, 'POST', p => p.endsWith('/members'));
  await groupThread.getByRole('button', { name: 'Adaugă', exact: true }).click();
  check('the group creator adds a member (201)', (await memberAdded).status() === 201);
  await groupThread.getByText(receptionist.full_name).first().waitFor();

  // ===== Task from creation to resolution =====
  await tab(manager, /^Sarcini/);
  const newTask = region(manager, 'Sarcină nouă');
  const addTask = async (title, due) => {
    await newTask.getByLabel('Titlu').fill(title);
    await newTask.getByLabel('Responsabil').selectOption(assistant.id);
    await newTask.getByLabel(/^Termen/).fill(due);
    const made = respond(manager, 'POST', p => p === '/api/collab/tasks');
    await newTask.getByRole('button', { name: 'Adaugă sarcina' }).click();
    return made;
  };
  const dueLocal = clinicLocal(new Date(Date.now() + 26 * 3600000));
  const createdResponse = await addTask('Verifică sterilizatorul (fictiv)', dueLocal);
  check('the manager creates a task (201)', createdResponse.status() === 201);
  const createdTask = await createdResponse.json();
  check('the due time keeps the clinic wall clock', clinicLocal(new Date(createdTask.due_at)) === dueLocal);
  check('a second task stays unresolved for the handover', (await addTask('Comandă mănuși (fictiv)', dueLocal)).status() === 201);
  await tab(assistantPage, /^Sarcini/);
  await taskRow(assistantPage, 'Verifică sterilizatorul (fictiv)').waitFor();
  check('the assistant starts the task', await applyTask(assistantPage, 'Verifică sterilizatorul (fictiv)', 'in_progress') === 200);
  await taskStatus(taskRow(assistantPage, 'Verifică sterilizatorul (fictiv)'), 'În lucru');
  const blockRow = taskRow(assistantPage, 'Verifică sterilizatorul (fictiv)');
  await blockRow.getByLabel('Status').selectOption('blocked');
  check('blocking needs a reason in the UI', await blockRow.getByRole('button', { name: 'Aplică' }).isDisabled());
  await blockRow.getByLabel('Status').selectOption('');
  check('the assistant blocks it with a reason', await applyTask(assistantPage, 'Verifică sterilizatorul (fictiv)', 'blocked', 'Lipsește piesa (fictiv)') === 200);
  await taskStatus(blockRow, 'Blocată');
  const stale = await assistantApi.call('POST', `/collab/tasks/${createdTask.id}/changes`, { expected_version: 1, status: 'resolved' });
  check('a stale version is refused (409)', stale.status === 409, { status: stale.status });
  check('the assistant resolves it', await applyTask(assistantPage, 'Verifică sterilizatorul (fictiv)', 'resolved') === 200);
  await assistantPage.getByRole('group', { name: 'Secțiuni' }).waitFor();
  await taskStatus(blockRow, 'Rezolvată');
  const finished = (await assistantApi.call('GET', '/collab/tasks?mine=true&status=resolved')).body.find(t => t.id === createdTask.id);
  check('every change is a history record with author', finished.events.length === 4 && finished.events[2].reason === 'Lipsește piesa (fictiv)' && finished.events.every(e => e.actor));
  await blockRow.locator('summary').click();
  check('the history is visible with the blocking reason', (await blockRow.innerText()).includes('Lipsește piesa (fictiv)'));
  await shot(assistantPage, 'assistant-task-resolved');
  check('the doctor does not see a task of someone else', !(await doctorApi.call('GET', '/collab/tasks')).body.some(t => t.id === createdTask.id));

  // ===== Shift handover, confirmed by the recipient =====
  await tab(assistantPage, /^Predări/);
  const handoverForm = region(assistantPage, 'Predare nouă');
  await handoverForm.getByLabel('Tura').locator('option', { hasText: assistant.full_name }).first().waitFor({ state: 'attached' });
  await handoverForm.getByLabel('Tura').selectOption({ index: 1 });
  await handoverForm.getByLabel('Predau către').selectOption(doctor.id);
  await handoverForm.getByLabel('Note').fill('Note fictive de predare');
  const handed = respond(assistantPage, 'POST', p => p === '/api/collab/handovers');
  await handoverForm.getByRole('button', { name: 'Salvează predarea' }).click();
  const handedResponse = await handed;
  const handedBody = await handedResponse.json();
  check('the assistant writes the handover (201)', handedResponse.status() === 201, { status: handedResponse.status() });
  check('the handover keeps only the unresolved task', handedBody.tasks.length === 1 && handedBody.tasks[0].title === 'Comandă mănuși (fictiv)');
  const rewrite = await assistantApi.call('POST', '/collab/handovers', { shift_id: handedBody.shift_id, to_user_id: doctor.id, notes: 'Rescris' });
  check('a handover cannot be rewritten (409)', rewrite.status === 409, { status: rewrite.status });
  check('the sender cannot confirm it (403)', (await assistantApi.call('POST', `/collab/handovers/${handedBody.id}/acknowledge`)).status === 403);
  await doctorPage.reload({ waitUntil: 'domcontentloaded' });
  await doctorPage.getByRole('group', { name: 'Secțiuni' }).getByRole('button', { name: 'Predări (1)' }).click();
  const received = region(doctorPage, 'Predări').locator('article').first();
  await received.getByText('Note fictive de predare').waitFor();
  check('the recipient sees the frozen task list', (await received.innerText()).includes('Comandă mănuși (fictiv)'));
  const acknowledged = respond(doctorPage, 'POST', p => p.endsWith('/acknowledge'));
  await received.getByRole('button', { name: 'Confirm primirea' }).click();
  check('the recipient confirms the handover (200)', (await acknowledged).status() === 200);
  await received.getByText('Primire confirmată').waitFor();
  await shot(doctorPage, 'doctor-handover-confirmed');

  // ===== Polling stops while the page is hidden and resumes after =====
  await tab(doctorPage, /^Mesaje/);
  await pause(600);
  let polled = 0;
  const counter = r => { if (r.request().method() === 'GET' && new URL(r.url()).pathname.startsWith('/api/collab/')) polled += 1; };
  await doctorPage.evaluate(() => { Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' }); Object.defineProperty(document, 'hidden', { configurable: true, get: () => true }); document.dispatchEvent(new Event('visibilitychange')); });
  await pause(500);
  doctorPage.on('response', counter);
  await pause(17000);
  check('no polling while the page is hidden (17 s)', polled === 0, { polled });
  await doctorPage.evaluate(() => { delete document.visibilityState; delete document.hidden; document.dispatchEvent(new Event('visibilitychange')); });
  await pause(16500);
  doctorPage.off('response', counter);
  check('polling resumes when the page is visible again', polled > 0, { polled });

  // ===== Narrow screen =====
  await doctorPage.setViewportSize({ width: 390, height: 800 });
  await pause(200);
  const overflow = await doctorPage.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check('no horizontal page scroll at phone width', overflow <= 0, { overflow });
  await shot(doctorPage, 'doctor-phone');

  check('no page errors', summary.pageErrors.length === 0, { pageErrors: summary.pageErrors });
  check('no unexpected HTTP errors in the browser', summary.unexpectedHttp.length === 0, { unexpected: summary.unexpectedHttp });
  summary.result = 'PASS';
} catch (error) {
  summary.result = 'FAIL';
  summary.errors.push(String(error.message).slice(0, 800));
} finally {
  await browser.close();
  for (const session of [managerApi, doctorApi, receptionApi, assistantApi]) if (session) await session.ctx.dispose();
  await fs.writeFile(path.join(evidenceDir, 'summary.json'), JSON.stringify(summary, null, 2), { mode: 0o600 });
  console.log(JSON.stringify({ result: summary.result, checks: summary.checks.length, failed: summary.checks.filter(c => !c.passed).map(c => c.name), errors: summary.errors, unexpectedHttp: summary.unexpectedHttp, pageErrors: summary.pageErrors, artifacts: summary.artifacts }, null, 1));
}
process.exit(summary.result === 'PASS' ? 0 : 1);
