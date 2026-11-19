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
const diagnostics = { pageErrors: [], consoleErrors: [], apiResponses: [], visibleErrors: [] };
let activePage = null;
function observePage(page, role) {
  activePage = page;
  page.on('pageerror', error => diagnostics.pageErrors.push({ role, message: String(error.message).slice(0, 500) }));
  page.on('console', message => { if (message.type() === 'error') diagnostics.consoleErrors.push({ role, message: message.text().slice(0, 500) }); });
  page.on('response', response => {
    const url = new URL(response.url());
    if (!url.pathname.includes('/api/')) return;
    const record = { role, method: response.request().method(), path: url.pathname, status: response.status() };
    diagnostics.apiResponses.push(record);
    if (response.status() >= 400) response.json().then(body => { record.errorDetail = String(body?.detail ?? body?.message ?? '').slice(0, 500); }).catch(() => {});
    if (diagnostics.apiResponses.length > 80) diagnostics.apiResponses.shift();
  });
}
async function observeAction(page, label, predicate, action) {
  const responseResult = page.waitForResponse(predicate, { timeout: 8000 })
    .then(response => ({ response }), error => ({ waitError: String(error?.message ?? error).slice(0, 400) }));
  let actionError = null;
  try { await action(); } catch (error) { actionError = String(error?.message ?? error).slice(0, 700); }
  const observed = await responseResult;
  const visibleErrors = await page.locator('[role="alert"], .error').allInnerTexts().catch(() => []);
  diagnostics.visibleErrors = visibleErrors.map(message => message.trim().slice(0, 500)).filter(Boolean).slice(-10);
  const response = observed.response;
  const detail = {
    label,
    actionError,
    visibleErrors: diagnostics.visibleErrors,
    latestApiResponses: diagnostics.apiResponses.slice(-8),
    pageErrors: diagnostics.pageErrors.slice(-8),
    consoleErrors: diagnostics.consoleErrors.slice(-8),
    responseStatus: response?.status() ?? null,
    responsePath: response ? new URL(response.url()).pathname : null,
    responseErrorDetail: response && response.status() >= 400 ? String((await response.json().catch(() => ({})))?.detail ?? '').slice(0, 500) : null,
    responseWaitError: observed.waitError ?? null,
  };
  if (actionError || !response) {
    const error = new Error(`${label}: ${actionError ?? observed.waitError ?? 'matching API response not observed'}`);
    error.diagnostic = detail;
    throw error;
  }
  return response;
}
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
safe.browser = browser.version();
async function loginContext(account, role) {
  const context = await browser.newContext();
  const page = await context.newPage();
  observePage(page, role);
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
  await page.locator(`a[href="/patients/${id}"]`).click();
  await page.getByRole('heading', { level: 1 }).waitFor();
}
async function authApi(account, role) {
  const ctx = await request.newContext();
  const login = await ctx.post(`${api}/auth/login`, { data: { email: account.email, password: account.password } });
  check(`${role} API login`, login.ok(), { status: login.status() });
  const body = await login.json();
  await ctx.dispose();
  const authenticated = await request.newContext({ extraHTTPHeaders: { Authorization: `Bearer ${body.access_token}` } });
  return {
    get: (route, options) => authenticated.get(`${api}${route}`, options),
    post: (route, options) => authenticated.post(`${api}${route}`, options),
    patch: (route, options) => authenticated.patch(`${api}${route}`, options),
    dispose: () => authenticated.dispose(),
  };
}
try {
  const { context: doctorContext, page } = await loginContext(creds.doctor, 'doctor');
  await page.getByRole('heading', { name: 'Patients', exact: true }).waitFor();
  const patientLinks = await page.locator('.list article a[href^="/patients/"]').evaluateAll(links => links.map(a => ({ href: a.getAttribute('href'), name: a.innerText.trim() })));
  check('doctor sees seeded patients', patientLinks.length >= 2, { count: patientLinks.length });
  const patientA = patientLinks[0].href.split('/').at(-1);
  const patientB = patientLinks[1].href.split('/').at(-1);
  await visitPatient(page, patientA);
  await page.locator('.patient-odontogram').waitFor();
  const teeth = page.locator('.patient-odontogram [data-tooth-id]');
  await page.locator('.patient-odontogram [data-tooth-id="16"]').waitFor();
  const toothCount = await teeth.count();
  check('odontogram loaded in browser', toothCount === 32, { teeth: toothCount, patientA });
  const odontogramCount = await page.locator('.patient-odontogram').count();
  check('one odontogram instance on patient record', odontogramCount === 1, { odontogramCount, newFindingFormCount: await page.locator('form.og-new-finding').count() });
  const patientAHeading = await page.getByRole('heading', { level: 1 }).innerText();
  await screenshot(page, 'doctor-patient-a');

  const stateSelect = page.getByLabel('Starea dintelui 1.6');
  const beforeStatus = await stateSelect.inputValue();
  const afterStatus = beforeStatus === 'missing' ? 'present' : 'missing';
  const stateResult = await observeAction(page, 'save tooth state', r => r.request().method() === 'PUT' && /\/odontogram\/patients\/[^/]+\/teeth\/16$/.test(new URL(r.url()).pathname), () => stateSelect.selectOption(afterStatus));
  check('tooth state saved through API', stateResult.status() === 200, { status: stateResult.status(), beforeStatus, afterStatus });

  const draftDescription = uniq('doctor draft');
  const newFindingForm = page.locator('form.og-new-finding');
  await newFindingForm.getByLabel('Tipul constatării').selectOption({ label: 'Carie' });
  await newFindingForm.getByLabel('Descriere', { exact: true }).fill(draftDescription);
  await newFindingForm.locator('button.og-surface[aria-label="Ocluzală (occlusal)"]').click();
  const createResponse = await observeAction(page, 'create doctor finding draft', r => r.request().method() === 'POST' && /\/odontogram\/patients\/[^/]+\/findings$/.test(new URL(r.url()).pathname), () => newFindingForm.getByRole('button', { name: 'Salvează ciornă' }).click());
  check('doctor finding draft created', createResponse.status() === 201, { status: createResponse.status() });
  let finding = await createResponse.json();
  const findingId = finding.id;
  const observedAt = finding.observedAt;
  const editedDescription = uniq('doctor draft edited');
  const card = page.locator(`.og-finding[data-finding-id="${findingId}"]`);
  check('finding draft has one rendered card', await card.count() === 1, { count: await card.count() });
  await card.waitFor();
  await card.getByRole('button', { name: 'Editează ciorna' }).click();
  await page.getByLabel(`Descriere ciornă ${findingId}`).fill(editedDescription);
  const patchResponse = await observeAction(page, 'edit doctor finding draft', r => r.request().method() === 'PATCH' && new URL(r.url()).pathname.endsWith(`/api/odontogram/findings/${findingId}`), () => card.getByRole('button', { name: 'Salvează ciorna' }).click());
  finding = await patchResponse.json();
  check('draft edit persisted', patchResponse.status() === 200 && finding.description === editedDescription, { status: patchResponse.status(), findingId });
  await page.reload();
  await page.locator('.patient-odontogram').waitFor();
  await page.getByText(editedDescription, { exact: true }).waitFor();
  check('draft survives reload with author/date', true, { author: finding.author, observedAt, status: finding.status });
  await screenshot(page, 'doctor-draft-after-reload');

  const finalizeResponse = await observeAction(page, 'finalize doctor finding', r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/api/odontogram/findings/${findingId}/finalize`), () => page.locator(`.og-finding[data-finding-id="${findingId}"]`).getByRole('button', { name: 'Finalizează explicit' }).click());
  finding = await finalizeResponse.json();
  check('finding finalized', finalizeResponse.status() === 200 && finding.status === 'finalized', { status: finalizeResponse.status(), statusValue: finding.status });
  const correctedDescription = uniq('doctor corrected');
  const reason = uniq('correction reason');
  const finalCard = page.locator(`.og-finding[data-finding-id="${findingId}"]`);
  await finalCard.getByRole('button', { name: 'Corectează constatarea' }).click();
  await page.getByLabel(`Descriere corectată pentru constatarea ${findingId}`).fill(correctedDescription);
  await page.getByLabel(`Motiv corecție ${findingId}`).fill(reason);
  const correctResponse = await observeAction(page, 'correct finalized finding', r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/api/odontogram/findings/${findingId}/corrections`), () => finalCard.getByRole('button', { name: 'Salvează corecția' }).click());
  finding = await correctResponse.json();
  check('correction stored with revision', correctResponse.status() === 200 && finding.description === correctedDescription && finding.revisions?.length >= 1, { status: correctResponse.status(), revisions: finding.revisions?.length });
  await page.getByText(`Motiv: ${reason}`, { exact: true }).waitFor();
  await page.reload();
  await page.getByText(correctedDescription, { exact: true }).waitFor();
  await page.getByText(`Motiv: ${reason}`, { exact: true }).waitFor();
  check('correction history survives reload', true, { findingId, revisions: finding.revisions.length, author: finding.revisions.at(-1)?.author, observedAt });

  await visitPatient(page, patientB);
  await page.locator('.patient-odontogram').waitFor();
  check('patient switch loads separate odontogram', await page.getByText(correctedDescription, { exact: true }).count() === 0, { patientB });
  await visitPatient(page, patientA);
  await page.getByText(correctedDescription, { exact: true }).waitFor();
  check('return to patient restores persisted finding', true, { patientA });

  // Keep a doctor-owned draft for the role-privacy check.
  await page.locator('[data-tooth-id="15"]').click();
  const privateDraftDescription = uniq('private doctor draft');
  const privateFindingForm = page.locator('form.og-new-finding');
  await privateFindingForm.getByLabel('Tipul constatării').selectOption({ label: 'Carie' });
  await privateFindingForm.getByLabel('Descriere', { exact: true }).fill(privateDraftDescription);
  const privateCreate = await observeAction(page, 'create private doctor finding', r => r.request().method() === 'POST' && /\/odontogram\/patients\/[^/]+\/findings$/.test(new URL(r.url()).pathname), () => privateFindingForm.getByRole('button', { name: 'Salvează ciornă' }).click());
  const privateFinding = await privateCreate.json();
  check('second doctor draft created', privateCreate.status() === 201 && privateFinding.status === 'draft', { status: privateCreate.status() });

  // Create and finalize T05 note entirely through the UI.
  const noteTitle = uniq('timeline note');
  const noteContent = uniq('timeline content');
  await page.getByRole('button', { name: 'New note or finding' }).click();
  await page.getByLabel('Title (optional)').fill(noteTitle);
  await page.getByLabel('Clinical content').fill(noteContent);
  const noteCreate = await observeAction(page, 'create T05 note', r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/clinical-notes', () => page.getByRole('button', { name: 'Save as draft' }).click());
  check('T05 draft note created through UI', noteCreate.status() === 201, { status: noteCreate.status() });
  const note = await noteCreate.json();
  const noteCard = page.locator('.clinical-list-item').filter({ has: page.getByRole('heading', { name: noteTitle, exact: true }) });
  await noteCard.waitFor();
  let noteFinalizeObserved = false;
  const timelineResponseP = page.waitForResponse(r => {
    const pathname = new URL(r.url()).pathname;
    if (r.request().method() === 'POST' && pathname.endsWith(`/api/clinical-notes/${note.id}/finalize`) && r.status() === 200) {
      noteFinalizeObserved = true;
      return false;
    }
    return noteFinalizeObserved && r.request().method() === 'GET' && pathname.endsWith(`/api/clinical-notes/patients/${patientA}/timeline`);
  }, { timeout: 8000 }).then(response => response, () => null);
  const noteFinalize = await observeAction(page, 'finalize T05 note', r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/api/clinical-notes/${note.id}/finalize`), () => noteCard.getByRole('button', { name: 'Finalize note' }).click());
  const timelineRes = await timelineResponseP;
  check('timeline request observed after finalize', Boolean(timelineRes), { requestObserved: Boolean(timelineRes) });
  check('T05 note finalized via API', noteFinalize.status() === 200, { status: noteFinalize.status() });
  const events = await timelineRes.json();
  const noteEvents = events.filter(e => e.entity_id === note.id || e.source_id === note.id || e.title?.includes(noteTitle));
  check('automatic timeline projection after finalize', noteEvents.length > 0 && timelineRes.status() === 200, { status: timelineRes.status(), eventCount: noteEvents.length, refreshEndpointUsed: false });
  await page.getByRole('heading', { name: 'Timeline', exact: true }).waitFor();
  await page.getByText(noteTitle, { exact: true }).waitFor();
  const noteEventIdsBefore = noteEvents.map(e => e.id).sort();
  await page.reload();
  await page.getByText(noteTitle, { exact: true }).waitFor();
  const doctorApi = await authApi(creds.doctor, 'doctor timeline reload');
  const timelineReloadResponse = await doctorApi.get(`/clinical-notes/patients/${patientA}/timeline`);
  const timelineAfter = await timelineReloadResponse.json();
  await doctorApi.dispose();
  const noteEventsAfterReload = timelineAfter.filter(e => e.entity_id === note.id || e.source_id === note.id || e.title?.includes(noteTitle)).map(e => e.id).sort();
  check('timeline reload stable and duplicate-free', noteEventIdsBefore.length > 0 && JSON.stringify(noteEventIdsBefore) === JSON.stringify(noteEventsAfterReload) && new Set(timelineAfter.map(e => e.id)).size === timelineAfter.length, { noteEventIdsBefore, noteEventIdsAfter: noteEventsAfterReload, totalEvents: timelineAfter.length, duplicates: timelineAfter.length - new Set(timelineAfter.map(e => e.id)).size });
  await screenshot(page, 'doctor-timeline-after-reload');

  // T07 path one: start from a selected tooth in the patient chart.
  await page.locator('[data-tooth-id="16"]').click();
  await page.getByRole('button', { name: 'Add performed procedure for this tooth' }).click();
  const entry = page.getByRole('region', { name: 'Add procedure for selected tooth' });
  await entry.getByLabel('Procedure visit').waitFor();
  const eligibleVisit = entry.getByLabel('Procedure visit');
  const eligibleOptions = await eligibleVisit.locator('option').evaluateAll(options => options.map(option => option.value).filter(Boolean));
  check('T07 chart path has eligible visit', eligibleOptions.length > 0, { eligibleVisitCount: eligibleOptions.length });
  await eligibleVisit.selectOption(eligibleOptions[0]);
  const chartProcedureForm = entry.locator('form.t07-procedure-form');
  await chartProcedureForm.waitFor();
  const chartCatalogOptions = await chartProcedureForm.getByLabel('Procedure from active catalog').locator('option').evaluateAll(options => options.map(option => option.value).filter(Boolean));
  check('T07 chart path has active procedure', chartCatalogOptions.length > 0, { catalogCount: chartCatalogOptions.length });
  await chartProcedureForm.getByLabel('Procedure from active catalog').selectOption(chartCatalogOptions[0]);
  const firstProcedureNote = uniq('T07 chart path');
  await chartProcedureForm.getByLabel('Procedure notes (optional)').fill(firstProcedureNote);
  const chartCreate = await observeAction(page, 'create procedure from odontogram', r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/performed-procedures', () => chartProcedureForm.getByRole('button', { name: 'Save procedure' }).click());
  const chartProcedure = await chartCreate.json();
  check('T07 chart path saved one pending procedure', chartCreate.status() === 201 && chartProcedure.status === 'pending' && chartProcedure.tooth_fdi === '16', { status: chartCreate.status(), procedureStatus: chartProcedure.status, tooth: chartProcedure.tooth_fdi, procedureId: chartProcedure.id });

  // Reopen the patient record and use the second T07 path from the visit page.
  await page.reload();
  const visitUrl = `/appointments/${chartProcedure.appointment_id}/visit`;
  await page.goto(`${web}${visitUrl}`, { waitUntil: 'domcontentloaded' });
  await page.getByRole('heading', { name: 'Performed procedures', exact: true }).waitFor();
  const visitProcedureForm = page.locator('form.t07-procedure-form');
  await visitProcedureForm.waitFor();
  const visitCatalogOptions = await visitProcedureForm.getByLabel('Procedure from active catalog').locator('option').evaluateAll(options => options.map(option => option.value).filter(Boolean));
  check('T07 visit path has active procedure', visitCatalogOptions.length > 0, { catalogCount: visitCatalogOptions.length });
  const visitCatalogSelect = visitProcedureForm.getByLabel('Procedure from active catalog');
  const selectedCatalogId = chartProcedure.procedure_catalog_id;
  check('F06 selected service is the pre-existing procedure service', visitCatalogOptions.includes(selectedCatalogId), { selectedCatalogId });
  await visitCatalogSelect.selectOption(selectedCatalogId);
  await visitProcedureForm.getByLabel('Procedure tooth').selectOption('17');
  const secondProcedureNote = uniq('T07 visit path');
  const visitProcedureNotes = visitProcedureForm.getByLabel('Procedure notes (optional)');
  await visitProcedureNotes.fill(secondProcedureNote);

  // F06: a stale active-catalog selection cannot create after deactivation.
  const f06ManagerApi = await authApi(creds.manager, 'F06 catalog lifecycle');
  const f06DoctorApi = await authApi(creds.doctor, 'F06 procedure preservation');
  const deactivatedCatalog = await f06ManagerApi.post(`/procedures/${selectedCatalogId}/deactivate`);
  const deactivatedCatalogJson = await deactivatedCatalog.json();
  check('F06 service deactivated while visit form remains open', deactivatedCatalog.status() === 200 && deactivatedCatalogJson.is_active === false, { status: deactivatedCatalog.status(), isActive: deactivatedCatalogJson.is_active });
  const staleCreate = await observeAction(page, 'reject stale inactive service selection', r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/performed-procedures', () => visitProcedureForm.getByRole('button', { name: 'Save procedure' }).click());
  const staleFormAlert = page.getByRole('alert').filter({ hasText: 'inactive catalog service' });
  await staleFormAlert.waitFor();
  check('F06 stale selection rejected with controlled message', staleCreate.status() === 409 && await staleFormAlert.count() === 1, { status: staleCreate.status(), visibleMessage: (await staleFormAlert.innerText()).trim() });
  check('F06 rejected create preserves selected service and form fields', await visitCatalogSelect.inputValue() === selectedCatalogId && await visitProcedureForm.getByLabel('Procedure tooth').inputValue() === '17' && await visitProcedureNotes.inputValue() === secondProcedureNote, { selectedServicePreserved: await visitCatalogSelect.inputValue() === selectedCatalogId, toothPreserved: await visitProcedureForm.getByLabel('Procedure tooth').inputValue() === '17', notesPreserved: await visitProcedureNotes.inputValue() === secondProcedureNote });
  const proceduresBeforeRetryResponse = await f06DoctorApi.get(`/performed-procedures?appointment_id=${chartProcedure.appointment_id}`);
  const proceduresBeforeRetry = await proceduresBeforeRetryResponse.json();
  check('F06 rejected create has no new procedure side effect', proceduresBeforeRetryResponse.ok() && !proceduresBeforeRetry.some(item => item.notes === secondProcedureNote), { status: proceduresBeforeRetryResponse.status(), matchingProcedures: proceduresBeforeRetry.filter(item => item.notes === secondProcedureNote).length });
  const existingProcedureCard = page.locator('article.procedure-card').filter({ hasText: firstProcedureNote });
  check('F06 pre-existing procedure remains visible while service is disabled', await existingProcedureCard.count() === 1 && await existingProcedureCard.getByText('pending', { exact: true }).count() > 0, { cardCount: await existingProcedureCard.count() });
  const existingProcedureStart = await observeAction(page, 'execute pre-existing procedure after catalog deactivation', r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/performed-procedures/${chartProcedure.id}/start`), () => existingProcedureCard.getByRole('button', { name: 'Start', exact: true }).click());
  const startedExistingProcedure = await existingProcedureStart.json();
  check('F06 pre-existing procedure remains executable after deactivation', existingProcedureStart.status() === 200 && startedExistingProcedure.status === 'running', { status: existingProcedureStart.status(), procedureStatus: startedExistingProcedure.status });
  const existingAfterDeactivate = await f06DoctorApi.get(`/performed-procedures/${chartProcedure.id}`);
  const existingAfterDeactivateJson = await existingAfterDeactivate.json();
  check('F06 pre-existing procedure snapshot remains readable', existingAfterDeactivate.ok() && existingAfterDeactivateJson.procedure_catalog_id === selectedCatalogId && existingAfterDeactivateJson.procedure_name === chartProcedure.procedure_name && existingAfterDeactivateJson.status === 'running', { status: existingAfterDeactivate.status(), procedureStatus: existingAfterDeactivateJson.status, snapshotNamePreserved: existingAfterDeactivateJson.procedure_name === chartProcedure.procedure_name });
  const reactivatedCatalog = await f06ManagerApi.post(`/procedures/${selectedCatalogId}/activate`);
  const reactivatedCatalogJson = await reactivatedCatalog.json();
  check('F06 service reactivated for remaining T07 acceptance', reactivatedCatalog.status() === 200 && reactivatedCatalogJson.is_active === true, { status: reactivatedCatalog.status(), isActive: reactivatedCatalogJson.is_active });
  const visitCreate = await observeAction(page, 'create procedure from visit after service reactivation', r => r.request().method() === 'POST' && new URL(r.url()).pathname === '/api/performed-procedures', () => visitProcedureForm.getByRole('button', { name: 'Save procedure' }).click());
  const visitProcedure = await visitCreate.json();
  check('T07 visit path saved one pending procedure', visitCreate.status() === 201 && visitProcedure.status === 'pending' && visitProcedure.tooth_fdi === '17', { status: visitCreate.status(), procedureStatus: visitProcedure.status, tooth: visitProcedure.tooth_fdi, procedureId: visitProcedure.id });
  await f06ManagerApi.dispose();
  await f06DoctorApi.dispose();
  const visitApi = await authApi(creds.doctor, 'T07 procedure persistence');
  const visitProceduresResponse = await visitApi.get(`/performed-procedures?appointment_id=${chartProcedure.appointment_id}`);
  const savedVisitProcedures = await visitProceduresResponse.json();
  check('both T07 paths persisted exactly once', visitProceduresResponse.ok() && savedVisitProcedures.filter(item => [chartProcedure.id, visitProcedure.id].includes(item.id)).length === 2 && new Set(savedVisitProcedures.map(item => item.id)).size === savedVisitProcedures.length, { status: visitProceduresResponse.status(), count: savedVisitProcedures.length, ids: savedVisitProcedures.map(item => item.id), duplicates: savedVisitProcedures.length - new Set(savedVisitProcedures.map(item => item.id)).size });
  await page.reload();
  await page.getByText(firstProcedureNote, { exact: true }).waitFor();
  await page.getByText(secondProcedureNote, { exact: true }).waitFor();
  const persistedChartPathCount = await page.locator('article.procedure-card').filter({ hasText: firstProcedureNote }).count();
  const persistedVisitPathCount = await page.locator('article.procedure-card').filter({ hasText: secondProcedureNote }).count();
  check('both T07 procedures survive visit reload', persistedChartPathCount === 1 && persistedVisitPathCount === 1, { persistedChartPathCount, persistedVisitPathCount });

  // Explicit timer lifecycle on the chart-path procedure.
  const timerCard = page.locator('article.procedure-card').filter({ hasText: firstProcedureNote });
  check('timer target resolves to one procedure', await timerCard.count() === 1, { count: await timerCard.count() });
  const timerActions = [
    ['pause', 'Pause', 'paused'], ['resume', 'Resume', 'running'], ['complete', 'Complete', 'completed'],
  ];
  for (const [transition, label, expectedStatus] of timerActions) {
    const response = await observeAction(page, `explicit procedure timer ${transition}`, r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/performed-procedures/${chartProcedure.id}/${transition}`), () => timerCard.getByRole('button', { name: label, exact: true }).click());
    const result = await response.json();
    check(`timer transition ${transition} persisted`, response.status() === 200 && result.status === expectedStatus, { status: response.status(), procedureStatus: result.status });
  }
  await page.reload();
  await page.getByText(firstProcedureNote, { exact: true }).waitFor();
  check('completed timer persists after reload', await timerCard.getByText('completed', { exact: true }).count() > 0, { statusVisible: await timerCard.getByText('completed', { exact: true }).count() > 0 });

  // Resolve the second pending procedure, then close the visit as a separate UI action.
  const secondCard = page.locator('article.procedure-card').filter({ hasText: secondProcedureNote });
  check('second T07 procedure has one card', await secondCard.count() === 1, { count: await secondCard.count() });
  const cancelResponse = await observeAction(page, 'resolve second T07 procedure', r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/performed-procedures/${visitProcedure.id}/cancel`), () => secondCard.getByRole('button', { name: 'Cancel', exact: true }).click());
  check('second T07 procedure resolved', cancelResponse.status() === 200, { status: cancelResponse.status() });
  for (const [status, label] of [['confirmed', 'Mark confirmed'], ['arrived', 'Mark arrived'], ['in_progress', 'Mark in progress']]) {
    const response = await observeAction(page, `advance visit to ${status}`, r => r.request().method() === 'PATCH' && new URL(r.url()).pathname.endsWith(`/appointments/${chartProcedure.appointment_id}/status`), () => page.getByRole('button', { name: label, exact: true }).click());
    const result = await response.json();
    check(`visit status ${status} saved`, response.status() === 200 && result.status === status, { status: response.status(), appointmentStatus: result.status });
  }
  const closeResponse = await observeAction(page, 'close visit separately', r => r.request().method() === 'PATCH' && new URL(r.url()).pathname.endsWith(`/appointments/${chartProcedure.appointment_id}/status`), () => page.getByRole('button', { name: 'Close visit', exact: true }).click());
  const closedVisit = await closeResponse.json();
  check('visit closed as a separate action', closeResponse.status() === 200 && closedVisit.status === 'completed', { status: closeResponse.status(), appointmentStatus: closedVisit.status });
  await visitApi.dispose();
  await screenshot(page, 'doctor-visit-timer-and-close');
  await doctorContext.close();

  // Separate browser context and API client for manager.
  const managerSession = await loginContext(creds.manager, 'manager');
  const managerPage = managerSession.page;
  await visitPatient(managerPage, patientA);
  await managerPage.locator('.patient-odontogram').waitFor();
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
  const targetCard = managerPage.locator(`.og-finding[data-finding-id="${findingId}"]`);
  await targetCard.getByRole('button', { name: 'Corectează constatarea' }).waitFor();
  await targetCard.getByRole('button', { name: 'Corectează constatarea' }).click();
  await managerPage.getByLabel(`Descriere corectată pentru constatarea ${findingId}`).fill(managerCorrection);
  await managerPage.getByLabel(`Motiv corecție ${findingId}`).fill(managerReason);
  const managerCorrect = await observeAction(managerPage, 'manager correct finalized finding', r => r.request().method() === 'POST' && new URL(r.url()).pathname.endsWith(`/api/odontogram/findings/${findingId}/corrections`), () => targetCard.getByRole('button', { name: 'Salvează corecția' }).click());
  const managerFinding = await managerCorrect.json();
  check('manager may correct finalized finding', managerCorrect.status() === 200 && managerFinding.description === managerCorrection, { status: managerCorrect.status(), revisionAuthor: managerFinding.revisions.at(-1)?.author });
  const managerDescription = uniq('manager own finding');
  await managerPage.locator('[data-tooth-id="14"]').click();
  const managerFindingForm = managerPage.locator('form.og-new-finding');
  await managerFindingForm.getByLabel('Tipul constatării').selectOption({ label: 'Carie' });
  await managerFindingForm.getByLabel('Descriere', { exact: true }).fill(managerDescription);
  const managerCreate = await observeAction(managerPage, 'create manager finding', r => r.request().method() === 'POST' && /\/odontogram\/patients\/[^/]+\/findings$/.test(new URL(r.url()).pathname), () => managerFindingForm.getByRole('button', { name: 'Salvează ciornă' }).click());
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
  check('no browser page errors', diagnostics.pageErrors.length === 0, { count: diagnostics.pageErrors.length });
  safe.result = 'passed';
} catch (error) {
  safe.result = 'failed';
  const visibleErrors = activePage ? await activePage.locator('[role="alert"], .error').allInnerTexts().catch(() => []) : [];
  safe.errors.push({ message: String(error?.message ?? error).slice(0, 700), diagnostic: error?.diagnostic ?? null, visibleErrors: visibleErrors.map(message => message.trim().slice(0, 500)).filter(Boolean).slice(-10), pageErrors: diagnostics.pageErrors.slice(-10), consoleErrors: diagnostics.consoleErrors.slice(-10), apiResponses: diagnostics.apiResponses.slice(-20) });
  if (activePage) await screenshot(activePage, 'failure').catch(() => {});
} finally {
  await browser.close();
  safe.diagnostics = diagnostics;
  const output = path.join(evidenceDir, `${runId}-summary.json`);
  await fs.writeFile(output, `${JSON.stringify(safe, null, 2)}\n`, { mode: 0o600 });
  console.log(JSON.stringify({ result: safe.result, browser: safe.browser, checks: safe.checks, errors: safe.errors, artifacts: safe.artifacts, summary: output }));
  if (safe.result !== 'passed') process.exitCode = 1;
}
