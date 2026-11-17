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
const safe = { appSha: process.env.DENTACARE_APP_SHA, web, api, browser: null, checks: [], artifacts: [], errors: [] };
const check = (name, ok, details = {}) => { safe.checks.push({ name, passed: Boolean(ok), ...details }); if (!ok) throw new Error(`Acceptance failed: ${name}`); };
const screenshot = async (page, name) => { const file = path.join(evidenceDir, `${runId}-${name}.png`); await page.screenshot({ path: file, fullPage: true }); safe.artifacts.push(file); };
const uniq = (label) => `Pilot fictiv ${label} ${runId}`;
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
safe.browser = browser.version();
const failures = [];
async function loginContext(account, role) {
  const context = await browser.newContext();
  const page = await context.newPage();
  page.on('pageerror', e => failures.push(`${role}: pageerror`));
  await page.goto(web, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill(account.email);
  await page.getByLabel('Password').fill(account.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Patients', exact: true }).waitFor();
  await page.getByRole('link', { name: 'Patients', exact: true }).click();
  await page.getByRole('heading', { name: 'Patients', exact: true }).waitFor();
  return { context, page };
}
async function visitPatient(page, id) {
  await page.getByRole('link', { name: 'Patients', exact: true }).click();
  await page.locator(`a[href="/patients/${id}"]`).first().click();
  await page.locator('h1').first().waitFor();
}
async function authApi(account, role) {
  const ctx = await request.newContext({ baseURL: api });
  const login = await ctx.post('/auth/login', { data: { email: account.email, password: account.password } });
  check(`${role} API login`, login.ok(), { status: login.status() });
  const body = await login.json();
  await ctx.setExtraHTTPHeaders({ Authorization: `Bearer ${body.access_token}` });
  return ctx;
}
try {
  const { context: doctorContext, page } = await loginContext(creds.doctor, 'doctor');
  await page.getByRole('heading', { name: 'Patients', exact: true }).waitFor();
  const patientLinks = await page.locator('.list article a[href^="/patients/"]').evaluateAll(links => links.map(a => ({ href: a.getAttribute('href'), name: a.innerText.trim() })));
  check('doctor sees seeded patients', patientLinks.length >= 2, { count: patientLinks.length });
  const patientA = patientLinks[0].href.split('/').at(-1);
  const patientB = patientLinks[1].href.split('/').at(-1);
  await visitPatient(page, patientA);
  await page.locator('.patient-odontogram').first().waitFor();
  const teeth = page.locator('.patient-odontogram').first().locator('[data-tooth-id]');
  await teeth.first().waitFor();
  const toothCount = await teeth.count();
  check('odontogram loaded in browser', toothCount === 32, { teeth: toothCount, patientA });
  const patientAHeading = await page.locator('h1').first().innerText();
  await screenshot(page, 'doctor-patient-a');

  const stateSelect = page.locator('[aria-label^="Starea dintelui"]').last();
  const beforeStatus = await stateSelect.inputValue();
  const afterStatus = beforeStatus === 'missing' ? 'present' : 'missing';
  const stateResponse = page.waitForResponse(r => r.request().method() === 'PUT' && /\/odontogram\/patients\/[^/]+\/teeth\/16$/.test(new URL(r.url()).pathname));
  await stateSelect.selectOption(afterStatus);
  const stateResult = await stateResponse;
  check('tooth state saved through API', stateResult.status() === 200, { status: stateResult.status(), beforeStatus, afterStatus });

  const draftDescription = uniq('doctor draft');
  await page.getByLabel('Tipul constatării').last().selectOption({ label: 'Carie' });
  await page.getByLabel('Descriere', { exact: true }).last().fill(draftDescription);
  await page.getByRole('button', { name: /^Ocluzală/ }).last().click();
  const createResponseP = page.waitForResponse(r => r.request().method() === 'POST' && /\/odontogram\/patients\/[^/]+\/findings$/.test(new URL(r.url()).pathname));
  await page.getByRole('button', { name: 'Salvează ciornă' }).click();
  const createResponse = await createResponseP;
  check('doctor finding draft created', createResponse.status() === 201, { status: createResponse.status() });
  let finding = await createResponse.json();
  const findingId = finding.id;
  const observedAt = finding.observedAt;
  const editedDescription = uniq('doctor draft edited');
  const card = page.locator(`.og-finding[data-finding-id="${findingId}"]`).last();
  await card.waitFor();
  await card.getByRole('button', { name: 'Editează ciorna' }).click();
  await page.getByLabel(`Descriere ciornă ${findingId}`).fill(editedDescription);
  const patchResponseP = page.waitForResponse(r => r.request().method() === 'PATCH' && new URL(r.url()).pathname.endsWith(`/api/odontogram/findings/${findingId}`));
  await card.getByRole('button', { name: 'Salvează ciorna' }).click();
  const patchResponse = await patchResponseP;
  finding = await patchResponse.json();
  check('draft edit persisted', patchResponse.status() === 200 && finding.description === editedDescription, { status: patchResponse.status(), findingId });
  await page.reload();
  await page.locator('.patient-odontogram').first().waitFor();
  await page.getByText(editedDescription, { exact: true }).waitFor();
  check('draft survives reload with author/date', true, { author: finding.author, observedAt, status: finding.status });
  await screenshot(page, 'doctor-draft-after-reload');

  const finalizeP = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/api/odontogram/findings/${findingId}/finalize`));
  await page.locator(`.og-finding[data-finding-id="${findingId}"]`).last().getByRole('button', { name: 'Finalizează explicit' }).click();
  const finalizeResponse = await finalizeP;
  finding = await finalizeResponse.json();
  check('finding finalized', finalizeResponse.status() === 200 && finding.status === 'finalized', { status: finalizeResponse.status(), statusValue: finding.status });
  const correctedDescription = uniq('doctor corrected');
  const reason = uniq('correction reason');
  const finalCard = page.locator(`.og-finding[data-finding-id="${findingId}"]`).last();
  await finalCard.getByRole('button', { name: 'Corectează constatarea' }).click();
  await page.getByLabel(`Descriere corectată pentru constatarea ${findingId}`).fill(correctedDescription);
  await page.getByLabel(`Motiv corecție ${findingId}`).fill(reason);
  const correctP = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/api/odontogram/findings/${findingId}/corrections`));
  await finalCard.getByRole('button', { name: 'Salvează corecția' }).click();
  const correctResponse = await correctP;
  finding = await correctResponse.json();
  check('correction stored with revision', correctResponse.status() === 200 && finding.description === correctedDescription && finding.revisions?.length >= 1, { status: correctResponse.status(), revisions: finding.revisions?.length });
  await page.getByText(reason, { exact: true }).waitFor();
  await page.reload();
  await page.getByText(correctedDescription, { exact: true }).waitFor();
  await page.getByText(reason, { exact: true }).waitFor();
  check('correction history survives reload', true, { findingId, revisions: finding.revisions.length, author: finding.revisions.at(-1)?.author, observedAt });

  await visitPatient(page, patientB);
  await page.locator('.patient-odontogram').first().waitFor();
  check('patient switch loads separate odontogram', await page.getByText(correctedDescription, { exact: true }).count() === 0, { patientB });
  await visitPatient(page, patientA);
  await page.getByText(correctedDescription, { exact: true }).waitFor();
  check('return to patient restores persisted finding', true, { patientA });

  // Keep a doctor-owned draft for the role-privacy check.
  await page.locator('[data-tooth-id="15"]').click();
  const privateDraftDescription = uniq('private doctor draft');
  await page.getByLabel('Tipul constatării').last().selectOption({ label: 'Carie' });
  await page.getByLabel('Descriere', { exact: true }).last().fill(privateDraftDescription);
  const privateCreateP = page.waitForResponse(r => r.request().method() === 'POST' && /\/odontogram\/patients\/[^/]+\/findings$/.test(new URL(r.url()).pathname));
  await page.getByRole('button', { name: 'Salvează ciornă' }).click();
  const privateCreate = await privateCreateP;
  const privateFinding = await privateCreate.json();
  check('second doctor draft created', privateCreate.status() === 201 && privateFinding.status === 'draft', { status: privateCreate.status() });

  // Create and finalize T05 note entirely through the UI.
  const noteTitle = uniq('timeline note');
  const noteContent = uniq('timeline content');
  await page.getByRole('button', { name: 'New note or finding' }).click();
  await page.getByLabel('Title (optional)').fill(noteTitle);
  await page.getByLabel('Clinical content').fill(noteContent);
  const noteCreateP = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/clinical-notes');
  await page.getByRole('button', { name: 'Save as draft' }).click();
  const noteCreate = await noteCreateP;
  check('T05 draft note created through UI', noteCreate.status() === 201, { status: noteCreate.status() });
  const note = await noteCreate.json();
  const noteCard = page.locator('.clinical-list-item').filter({ has: page.getByRole('heading', { name: noteTitle, exact: true }) });
  await noteCard.waitFor();
  const timelineResponse = page.waitForResponse(r => r.request().method() === 'GET' && new URL(r.url()).pathname.endsWith(`/api/clinical-notes/patients/${patientA}/timeline`));
  await noteCard.getByRole('button', { name: 'Finalize note' }).click();
  const noteFinalizeP = page.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/api/clinical-notes/${note.id}/finalize`));
  const noteFinalize = await noteFinalizeP;
  const timelineRes = await timelineResponse;
  check('T05 note finalized via API', noteFinalize.status() === 200, { status: noteFinalize.status() });
  const events = await timelineRes.json();
  const noteEvents = events.filter(e => e.entity_id === note.id || e.source_id === note.id || e.title?.includes(noteTitle));
  check('automatic timeline projection after finalize', noteEvents.length > 0 && timelineRes.status() === 200, { status: timelineRes.status(), eventCount: noteEvents.length, refreshEndpointUsed: false });
  await page.getByRole('heading', { name: 'Timeline', exact: true }).waitFor();
  await page.getByText(noteTitle, { exact: true }).waitFor();
  const eventIdsBefore = events.map(e => e.id);
  await page.reload();
  await page.getByText(noteTitle, { exact: true }).waitFor();
  const doctorApi = await authApi(creds.doctor, 'doctor timeline reload');
  const timelineReloadResponse = await doctorApi.get(`/clinical-notes/patients/${patientA}/timeline`);
  const timelineAfter = await timelineReloadResponse.json();
  await doctorApi.dispose();
  check('timeline reload stable and duplicate-free', timelineAfter.length > 0 && new Set(timelineAfter.map(e => e.id)).size === timelineAfter.length && JSON.stringify(eventIdsBefore) === JSON.stringify(timelineAfter.map(e => e.id)), { ids: timelineAfter.map(e => e.id), duplicates: timelineAfter.length - new Set(timelineAfter.map(e => e.id)).size });
  await screenshot(page, 'doctor-timeline-after-reload');
  await doctorContext.close();

  // Separate browser context and API client for manager.
  const managerSession = await loginContext(creds.manager, 'manager');
  const managerPage = managerSession.page;
  await visitPatient(managerPage, patientA);
  await managerPage.locator('.patient-odontogram').first().waitFor();
  check('manager cannot see doctor draft in UI', await managerPage.getByText(privateDraftDescription, { exact: true }).count() === 0, { privateFindingId: privateFinding.id });
  const managerApi = await authApi(creds.manager, 'manager');
  const managerChart = await managerApi.get(`/odontogram/patients/${patientA}`);
  const managerChartJson = await managerChart.json();
  check('manager GET excludes doctor draft', managerChart.ok() && !managerChartJson.findings.some(f => f.id === privateFinding.id), { status: managerChart.status() });
  const foreignPatch = await managerApi.patch(`/odontogram/findings/${privateFinding.id}`, { data: { description: uniq('unauthorized edit') } });
  const foreignFinalize = await managerApi.post(`/odontogram/findings/${privateFinding.id}/finalize`);
  check('manager cannot edit/finalize doctor draft', foreignPatch.status() === 404 && foreignFinalize.status() === 404, { patch: foreignPatch.status(), finalize: foreignFinalize.status() });

  const managerCorrection = uniq('manager correction');
  const managerReason = uniq('manager correction reason');
  const targetCard = managerPage.locator(`.og-finding[data-finding-id="${findingId}"]`).last();
  await targetCard.getByRole('button', { name: 'Corectează constatarea' }).waitFor();
  await targetCard.getByRole('button', { name: 'Corectează constatarea' }).click();
  await managerPage.getByLabel(`Descriere corectată pentru constatarea ${findingId}`).fill(managerCorrection);
  await managerPage.getByLabel(`Motiv corecție ${findingId}`).fill(managerReason);
  const managerCorrectP = managerPage.waitForResponse(r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/api/odontogram/findings/${findingId}/corrections`));
  await targetCard.getByRole('button', { name: 'Salvează corecția' }).click();
  const managerCorrect = await managerCorrectP;
  const managerFinding = await managerCorrect.json();
  check('manager may correct finalized finding', managerCorrect.status() === 200 && managerFinding.description === managerCorrection, { status: managerCorrect.status(), revisionAuthor: managerFinding.revisions.at(-1)?.author });
  const managerDescription = uniq('manager own finding');
  await managerPage.locator('[data-tooth-id="14"]').click();
  await managerPage.getByLabel('Tipul constatării').last().selectOption({ label: 'Carie' });
  await managerPage.getByLabel('Descriere', { exact: true }).last().fill(managerDescription);
  const managerCreateP = managerPage.waitForResponse(r => r.request().method() === 'POST' && /\/odontogram\/patients\/[^/]+\/findings$/.test(new URL(r.url()).pathname));
  await managerPage.getByRole('button', { name: 'Salvează ciornă' }).click();
  const managerCreate = await managerCreateP;
  const managerFindingOwn = await managerCreate.json();
  check('manager creates own finding', managerCreate.status() === 201 && managerFindingOwn.status === 'draft' && await managerPage.getByText(managerDescription, { exact: true }).count() === 1, { status: managerCreate.status(), author: managerFindingOwn.author });
  await managerSession.context.close();
  await managerApi.dispose();

  // Reception gets a fresh isolated session and should have no clinical chart access.
  const reception = await loginContext(creds.reception, 'reception');
  await visitPatient(reception.page, patientA);
  check('reception UI has no odontogram', await reception.page.locator('.patient-odontogram').count() === 0, { patientA });
  const receptionApi = await authApi(creds.reception, 'reception');
  const receptionChart = await receptionApi.get(`/odontogram/patients/${patientA}`);
  check('reception API denied odontogram', receptionChart.status() === 403, { status: receptionChart.status() });
  await reception.context.close();
  await receptionApi.dispose();
  check('no browser page errors', failures.length === 0, { count: failures.length });
  safe.result = 'passed';
} catch (error) {
  safe.result = 'failed';
  safe.errors.push(String(error?.message ?? error).slice(0, 500));
} finally {
  await browser.close();
  const output = path.join(evidenceDir, `${runId}-summary.json`);
  await fs.writeFile(output, `${JSON.stringify(safe, null, 2)}\n`, { mode: 0o600 });
  console.log(JSON.stringify({ result: safe.result, browser: safe.browser, checks: safe.checks, errors: safe.errors, artifacts: safe.artifacts, summary: output }));
  if (safe.result !== 'passed') process.exitCode = 1;
}
