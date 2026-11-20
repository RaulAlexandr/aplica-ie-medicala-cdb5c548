// T01/T03 gaps browser acceptance against a running fictional pilot runtime (real API + SQLite).
// (a) The manager deactivates a fictional staff member in the UI, the member's login is refused and the old
//     tokens are dead; the manager reactivates from StaffDetail (with confirmation), the history shows who/when,
//     the old tokens stay invalid and a fresh login works.
// (b) The manager creates a "dinte obligatoriu" service in the catalog UI; the doctor cannot save it without a
//     tooth on both T07 paths (visit form and tooth → procedure) nor in a T08 plan; the server refuses the same
//     requests with 422; an existing seeded service still saves without a tooth exactly as before.
// Credentials and tokens never reach the summary.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const required = ['DENTACARE_PILOT_HOME', 'DENTACARE_APP_SHA', 'PILOT_WEB_URL', 'PILOT_API_URL', 'PILOT_CREDENTIALS', 'PLAYWRIGHT_MODULE', 'CHROMIUM_EXECUTABLE'];
for (const key of required) if (!process.env[key]) throw new Error(`Missing ${key}`);
const { chromium, request } = await import(pathToFileURL(process.env.PLAYWRIGHT_MODULE).href);
const creds = JSON.parse(await fs.readFile(process.env.PILOT_CREDENTIALS, 'utf8'));
const web = process.env.PILOT_WEB_URL.replace(/\/$/, '');
const api = process.env.PILOT_API_URL.replace(/\/$/, '');
const evidenceDir = process.env.T01T03_EVIDENCE_DIR || path.join(process.env.DENTACARE_PILOT_HOME, 'browser-evidence', 't01-t03');
await fs.mkdir(evidenceDir, { recursive: true, mode: 0o700 });
const runId = new Date().toISOString().replaceAll(':', '-');
const tag = runId.slice(11, 19).replaceAll('-', '');
const summary = { task: 'T01/T03 gaps', appSha: process.env.DENTACARE_APP_SHA, web, api, browser: null, checks: [], artifacts: [],
  pageErrors: [], consoleErrors: [], network: { reactivatePosts: 0, procedurePosts: 0, planPosts: 0 }, errors: [] };
const check = (name, ok, details = {}) => { summary.checks.push({ name, passed: Boolean(ok), ...details }); if (!ok) throw new Error(`Acceptance failed: ${name} ${JSON.stringify(details)}`); };
const shot = async (page, name) => { const file = path.join(evidenceDir, `t01-t03-${name}.png`); await page.screenshot({ path: file, fullPage: true }); summary.artifacts.push(path.basename(file)); };
const optionDisabled = locator => locator.evaluate(el => el.disabled);
const idPath = url => { try { return new URL(url).pathname.replace(/[0-9a-f]{8}-[0-9a-f-]{27}/g, ':id'); } catch { return null; } };

async function apiLogin(email, password) {
  const ctx = await request.newContext();
  const response = await ctx.post(`${api}/auth/login`, { data: { email, password } });
  const body = response.ok() ? await response.json() : null;
  await ctx.dispose();
  return { status: response.status(), tokens: body };
}
async function apiCall(token, method, url, data) {
  const ctx = await request.newContext();
  const response = await ctx.fetch(`${api}${url}`, { method, data, headers: token ? { Authorization: `Bearer ${token}` } : {} });
  const result = { status: response.status(), body: await response.json().catch(() => null) };
  await ctx.dispose();
  return result;
}
async function apiSession(account) {
  const login = await apiLogin(account.email, account.password);
  if (login.status !== 200) throw new Error(`API login failed: ${login.status}`);
  return (method, url, data) => apiCall(login.tokens.access_token, method, url, data);
}

const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
summary.browser = browser.version();
const contexts = [];
async function openContext(label) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 950 }, locale: 'ro-RO' });
  contexts.push(context);
  const page = await context.newPage();
  page.on('pageerror', error => summary.pageErrors.push({ user: label, message: String(error.message).slice(0, 300) }));
  page.on('console', msg => { if (msg.type() === 'error') summary.consoleErrors.push({ user: label, text: msg.text().slice(0, 200), sourcePath: idPath(msg.location()?.url ?? '') }); });
  page.on('response', response => { if (response.status() >= 400) summary.consoleErrors.push({ user: label, httpStatus: response.status(), method: response.request().method(), path: idPath(response.url()) }); });
  page.on('request', req => {
    const p = new URL(req.url()).pathname;
    if (req.method() !== 'POST') return;
    if (/\/api\/staff\/[^/]+\/reactivate$/.test(p)) summary.network.reactivatePosts += 1;
    if (p === '/api/performed-procedures') summary.network.procedurePosts += 1;
    if (/\/api\/treatment-plans\/patients\/[^/]+$/.test(p)) summary.network.planPosts += 1;
  });
  return { context, page };
}
async function signIn(page, account) {
  await page.goto(web, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill(account.email);
  await page.getByLabel('Password').fill(account.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
}
async function login(account, label) {
  const { context, page } = await openContext(label);
  await signIn(page, account);
  await page.getByRole('link', { name: 'Patients', exact: true }).waitFor();
  return { context, page };
}

let activePage = null;
try {
  const managerApi = await apiSession(creds.manager);
  const doctorApi = await apiSession(creds.doctor);
  const doctorMe = (await doctorApi('GET', '/auth/me')).body;

  // ---------------- (a) Staff deactivation → reactivation ----------------
  const member = { email: `t01-asistent-${tag}@example.com`, password: `Fictiv-${tag}-parola-lunga` };
  const invitation = await managerApi('POST', '/staff/invitations', { email: member.email, full_name: `Asistent Fictiv ${tag}`, role: 'assistant' });
  check('setup: invitation for a fictional assistant', invitation.status === 201, { status: invitation.status });
  const token = new URL(invitation.body.invitation_url, web).searchParams.get('token');
  const accepted = await apiCall(null, 'POST', `/staff/invitations/accept?token=${encodeURIComponent(token)}`, { password: member.password });
  check('setup: invitation accepted', accepted.status === 200, { status: accepted.status });
  const memberId = accepted.body.id;
  const oldSession = await apiLogin(member.email, member.password);
  check('member can sign in before deactivation (API)', oldSession.status === 200);

  const manager = await login(creds.manager, 'manager'); activePage = manager.page;
  const mpage = manager.page;
  await mpage.goto(`${web}/staff`, { waitUntil: 'domcontentloaded' });
  const row = mpage.locator('article.list-row', { hasText: member.email });
  await row.waitFor();
  await row.getByRole('button', { name: 'Deactivate' }).click();
  await mpage.getByText('Staff member deactivated.').waitFor();
  check('manager deactivated the member from the staff list', await row.innerText().then(text => text.includes('Inactive')));

  const refused = await openContext('member'); activePage = refused.page;
  await signIn(refused.page, member);
  await refused.page.getByRole('alert').waitFor();
  check('deactivated member: browser login refused', (await refused.page.getByRole('alert').innerText()).includes('Invalid email or password')
    && await refused.page.getByRole('link', { name: 'Patients', exact: true }).count() === 0);
  check('deactivated member: old access token rejected', (await apiCall(oldSession.tokens.access_token, 'GET', '/auth/me')).status === 401);

  activePage = mpage;
  await mpage.goto(`${web}/staff/${memberId}`, { waitUntil: 'domcontentloaded' });
  await mpage.getByRole('heading', { level: 1, name: `Asistent Fictiv ${tag}` }).waitFor();
  await mpage.getByRole('button', { name: 'Reactivează' }).click();
  const confirmBox = mpage.getByRole('group', { name: 'Confirmare reactivare' });
  check('reactivation asks for confirmation first', await confirmBox.isVisible() && summary.network.reactivatePosts === 0);
  await confirmBox.getByRole('button', { name: 'Renunță' }).click();
  check('cancel sends nothing', summary.network.reactivatePosts === 0 && await mpage.getByRole('button', { name: 'Reactivează' }).isVisible());
  await mpage.getByRole('button', { name: 'Reactivează' }).click();
  const reactivateResponse = mpage.waitForResponse(r => r.request().method() === 'POST' && r.url().endsWith(`/staff/${memberId}/reactivate`));
  await confirmBox.getByRole('button', { name: 'Confirmă reactivarea' }).click();
  check('reactivate POST succeeded once', (await reactivateResponse).status() === 200 && summary.network.reactivatePosts === 1);
  await mpage.getByRole('status').filter({ hasText: 'a fost reactivat' }).waitFor();
  await mpage.reload();
  await mpage.getByRole('heading', { level: 1, name: `Asistent Fictiv ${tag}` }).waitFor();
  const detailText = await mpage.locator('main').innerText();
  const historyItems = await mpage.locator('section.card', { hasText: 'Istoric cont' }).locator('li').allInnerTexts();
  check('after reload: member active, no reactivate button', detailText.includes('Active') && await mpage.getByRole('button', { name: 'Reactivează' }).count() === 0);
  check('history shows reactivation and deactivation by the manager', historyItems.length >= 2 && historyItems[0].startsWith('Reactivat') && historyItems[1].startsWith('Dezactivat')
    && historyItems.slice(0, 2).every(item => item.includes(creds.manager.name)), { history: historyItems.map(item => item.split(' · ')[0]) });
  await shot(mpage, 'staff-reactivated');

  check('after reactivation: old access token still rejected', (await apiCall(oldSession.tokens.access_token, 'GET', '/auth/me')).status === 401);
  check('after reactivation: old refresh token still rejected', (await apiCall(null, 'POST', '/auth/refresh', { refresh_token: oldSession.tokens.refresh_token })).status === 401);
  check('double reactivation refused with 409', (await managerApi('POST', `/staff/${memberId}/reactivate`)).status === 409);
  check('doctor cannot reactivate (403)', (await doctorApi('POST', `/staff/${memberId}/reactivate`)).status === 403);
  const relog = await openContext('member-again'); activePage = relog.page;
  await signIn(relog.page, member);
  await relog.page.getByRole('link', { name: 'Patients', exact: true }).waitFor();
  check('reactivated member signs in again in the browser', true);

  // ---------------- (b) Allowed locations ----------------
  activePage = mpage;
  const extCode = `T03-EXT-${tag}`;
  await mpage.goto(`${web}/procedures`, { waitUntil: 'domcontentloaded' });
  await mpage.getByRole('button', { name: 'New procedure', exact: true }).click();
  const catalogForm = mpage.locator('form', { hasText: 'Localizări permise' });
  check('catalog form offers allowed locations, default "any"', await catalogForm.getByLabel('Localizări permise').inputValue() === 'any');
  await catalogForm.getByLabel('Code').fill(extCode);
  await catalogForm.getByLabel('Name').fill(`Extracție fictivă ${tag}`);
  await catalogForm.getByLabel('Base price').fill('300.00');
  await catalogForm.getByLabel('Localizări permise').selectOption('tooth');
  const created = mpage.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/procedures');
  await catalogForm.getByRole('button', { name: 'Save procedure' }).click();
  const ext = await (await created).json();
  check('manager created a "dinte obligatoriu" service in the UI', ext.allowed_location === 'tooth' && ext.code === extCode, { allowed_location: ext.allowed_location });
  await mpage.locator('article.list-row', { hasText: extCode }).getByText('dinte obligatoriu').waitFor();
  const noTooth = await managerApi('POST', '/procedures', { code: `T03-CONS-${tag}`, name: `Consultație fictivă ${tag}`, base_price: '100.00', default_duration_minutes: 20, allowed_location: 'no_tooth' });
  check('setup: a "fără dinte" service (API)', noTooth.status === 201);

  const catalog = (await doctorApi('GET', '/procedures?active=true&limit=200')).body;
  const legacy = catalog.find(row => row.allowed_location === 'any' && !row.code.startsWith('T03-'));
  check('an existing seeded service is still "any"', Boolean(legacy), { code: legacy?.code });
  const patient = (await doctorApi('POST', '/patients', { first_name: 'Elena', last_name: `T03 Fictiv ${tag}` })).body;
  const rooms = (await doctorApi('GET', '/rooms')).body;
  const start = new Date(Date.now() + 2 * 86400000); start.setUTCHours(7, Math.floor(Math.random() * 6) * 10, 0, 0);
  const visit = await doctorApi('POST', '/appointments', { patient_id: patient.id, doctor_id: doctorMe.id, room_id: rooms[0].id, starts_at: start.toISOString(), duration_minutes: 30, appointment_type: 'Tratament T03 fictiv' });
  check('setup: fictional visit', visit.status === 201, { status: visit.status, detail: visit.body?.detail });

  // Server proof: same refusals regardless of entry path.
  const serverRefusal = await doctorApi('POST', '/performed-procedures', { appointment_id: visit.body.id, procedure_catalog_id: ext.id });
  check('server refuses "dinte obligatoriu" without a tooth (T04/T07 endpoint)', serverRefusal.status === 422 && serverRefusal.body.detail === `Service ${extCode} requires an FDI tooth`, { status: serverRefusal.status });

  const doctor = await login(creds.doctor, 'doctor'); activePage = doctor.page;
  const dpage = doctor.page;

  // T07 path 1: procedure → tooth (visit page).
  await dpage.goto(`${web}/appointments/${visit.body.id}/visit`, { waitUntil: 'domcontentloaded' });
  const visitForm = dpage.locator('form.t07-procedure-form');
  await visitForm.waitFor();
  const postsBefore = summary.network.procedurePosts;
  await visitForm.getByLabel('Procedure from active catalog').selectOption(ext.id);
  const save = visitForm.getByRole('button', { name: 'Save procedure' });
  const ruleNote = visitForm.locator('[role="note"][aria-label="Location rule"]');
  check('visit path: Save disabled for tooth-required service without a tooth', await save.isDisabled());
  check('visit path: rule message shown', (await ruleNote.count()) === 1 && (await ruleNote.innerText()).includes('cere un dinte FDI'));
  check('visit path: "No tooth" option disabled', await optionDisabled(visitForm.getByLabel('Procedure tooth').locator('option[value=""]')));
  await shot(dpage, 'visit-tooth-required-blocked');
  check('visit path: nothing was sent', summary.network.procedurePosts === postsBefore);
  await visitForm.getByLabel('Procedure tooth').selectOption('16');
  const extCreate = dpage.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/performed-procedures');
  await save.click();
  const extSaved = await extCreate;
  check('visit path: with tooth 16 the service saves', extSaved.status() === 201 && (await extSaved.json()).tooth_fdi === '16');

  // Existing service, unchanged behaviour: saves without a tooth.
  await visitForm.getByLabel('Procedure from active catalog').selectOption(legacy.id);
  check('existing service: tooth stays optional', !(await visitForm.getByLabel('Procedure tooth').isDisabled()) && !(await save.isDisabled())
    && await visitForm.locator('[role="note"][aria-label="Location rule"]').count() === 0);
  const legacyCreate = dpage.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/performed-procedures');
  await save.click();
  const legacySaved = await legacyCreate;
  check('existing service: saved without a tooth, as before', legacySaved.status() === 201 && (await legacySaved.json()).tooth_fdi === null);

  // T07 path 2: tooth → procedure (patient record odontogram).
  await dpage.goto(`${web}/patients/${patient.id}`, { waitUntil: 'domcontentloaded' });
  await dpage.locator('[data-tooth-id="16"]').click();
  await dpage.getByRole('button', { name: 'Add performed procedure for this tooth' }).click();
  const entry = dpage.getByRole('region', { name: 'Add procedure for selected tooth' });
  await entry.getByLabel('Procedure visit').selectOption(visit.body.id);
  const toothForm = entry.locator('form.t07-procedure-form');
  await toothForm.waitFor();
  const procedureSelect = toothForm.getByLabel('Procedure from active catalog');
  check('tooth path: tooth 16 preselected', await toothForm.getByLabel('Procedure tooth').inputValue() === '16');
  check('tooth path: "fără dinte" service is not offered', await optionDisabled(procedureSelect.locator(`option[value="${noTooth.body.id}"]`)));
  await procedureSelect.selectOption(ext.id);
  check('tooth path: "No tooth" cannot be chosen for the tooth-required service', await optionDisabled(toothForm.getByLabel('Procedure tooth').locator('option[value=""]'))
    && !(await toothForm.getByRole('button', { name: 'Save procedure' }).isDisabled()));
  const toothCreate = dpage.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/performed-procedures');
  await toothForm.getByRole('button', { name: 'Save procedure' }).click();
  const toothSaved = await toothCreate;
  check('tooth path: saved with tooth 16', toothSaved.status() === 201 && (await toothSaved.json()).tooth_fdi === '16');
  const forcedNoTooth = await doctorApi('POST', '/performed-procedures', { appointment_id: visit.body.id, procedure_catalog_id: noTooth.body.id, tooth_fdi: '16' });
  check('server refuses a tooth for a "fără dinte" service', forcedNoTooth.status === 422, { status: forcedNoTooth.status });

  // T08: plan item without a tooth is blocked in the form and refused by the server.
  await dpage.goto(`${web}/patients/${patient.id}`, { waitUntil: 'domcontentloaded' });
  const plans = dpage.getByRole('region', { name: 'Planuri de tratament' });
  await plans.getByRole('button', { name: 'Plan nou' }).click();
  await dpage.getByLabel('Titlul planului').fill(`Plan T03 fictiv ${tag}`);
  await dpage.getByLabel('Etapa 1 manopera 1 serviciu').selectOption(ext.id);
  await dpage.getByLabel('Etapa 2 manopera 1 serviciu').selectOption(legacy.id);
  const planPostsBefore = summary.network.planPosts;
  await dpage.getByRole('button', { name: 'Salvează planul' }).click();
  await dpage.getByRole('alert').filter({ hasText: `${extCode}: Serviciul cere un dinte FDI.` }).waitFor();
  check('plan: tooth-required item blocked without a tooth, nothing sent', summary.network.planPosts === planPostsBefore
    && await optionDisabled(dpage.getByLabel('Etapa 1 manopera 1 dinte').locator('option[value=""]')));
  await shot(dpage, 'plan-tooth-required-blocked');
  const planRefusal = await doctorApi('POST', `/treatment-plans/patients/${patient.id}`, { title: 'Forțat', stages: [{ title: 'E1', items: [{ procedure_catalog_id: ext.id }] }] });
  check('server refuses the plan item without a tooth (422)', planRefusal.status === 422 && planRefusal.body.detail === `Service ${extCode} requires an FDI tooth`, { status: planRefusal.status });
  await dpage.getByLabel('Etapa 1 manopera 1 dinte').selectOption('26');
  const planCreate = dpage.waitForResponse(r => r.request().method() === 'POST' && /\/api\/treatment-plans\/patients\/[^/]+$/.test(new URL(r.url()).pathname));
  await dpage.getByRole('button', { name: 'Salvează planul' }).click();
  const planSaved = await planCreate;
  const planBody = await planSaved.json();
  const planItems = planBody.stages.flatMap(stage => stage.items);
  check('plan: saved with tooth 26 and the existing service without a tooth', planSaved.status() === 201
    && planItems.some(item => item.procedure_catalog_id === ext.id && item.tooth_fdi === '26')
    && planItems.some(item => item.procedure_catalog_id === legacy.id && item.tooth_fdi === null));

  await mpage.goto(`${web}/procedures`, { waitUntil: 'domcontentloaded' });
  await mpage.locator('article.list-row', { hasText: extCode }).waitFor();
  await shot(mpage, 'catalog-allowed-locations');
  summary.result = 'PASS';
} catch (error) {
  summary.result = 'FAIL';
  summary.errors.push(String(error?.message ?? error).slice(0, 600));
  if (activePage) {
    summary.failure = { url: idPath(activePage.url()), alerts: await activePage.getByRole('alert').allInnerTexts().catch(() => []) };
    await shot(activePage, 'failure').catch(() => {});
  }
} finally {
  for (const context of contexts) await context.close().catch(() => {});
  await browser.close();
  const passed = summary.checks.filter(item => item.passed).length;
  const serialized = JSON.stringify(summary, null, 2);
  for (const account of [creds.manager, creds.doctor, creds.reception]) if (account?.password && serialized.includes(account.password)) throw new Error('credential leaked into summary');
  await fs.writeFile(path.join(evidenceDir, 't01-t03-browser-summary.json'), serialized, { mode: 0o600 });
  console.log(`T01/T03 browser ${summary.result}: ${passed}/${summary.checks.length} checks · page errors ${summary.pageErrors.length} · console/http errors ${summary.consoleErrors.length} · evidence ${evidenceDir}`);
  if (summary.result !== 'PASS') process.exitCode = 1;
}
