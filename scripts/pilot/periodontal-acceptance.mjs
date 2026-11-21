// Reproducible local browser/API acceptance for T09-T10 plus a T08 integration smoke.
// Requires a disposable fictitious-data runtime with the worktree backend and frontend.
// No database, credential, token, or cookie is written to this repository.
import { fileURLToPath, pathToFileURL } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

const playwrightPath = process.env.PLAYWRIGHT_MODULE;
if (!playwrightPath) throw new Error('Set PLAYWRIGHT_MODULE to the local Playwright entry point');
const { chromium, request } = await import(pathToFileURL(playwrightPath).href);
const web = process.env.PILOT_WEB_URL ?? 'http://127.0.0.1:5199';
const api = (process.env.PILOT_API_URL ?? 'http://127.0.0.1:8029/api').replace(/\/$/, '');
const executablePath = process.env.CHROMIUM_EXECUTABLE;
if (!executablePath) throw new Error('Set CHROMIUM_EXECUTABLE to the local Chromium executable');
const evidenceDir = process.env.PERIODONTAL_EVIDENCE_DIR ?? fileURLToPath(new URL('./evidence', import.meta.url));
await mkdir(evidenceDir, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath, args: ['--no-sandbox'] });
const checks = [];
const consoleErrors = [];
const check = (name, ok, details = {}) => {
  checks.push({ name, ok, ...details });
  if (!ok) throw new Error(`${name}: ${JSON.stringify(details)}`);
};
const expectStatus = async (response, status, label) => {
  const body = await response.json().catch(() => null);
  check(label, response.status() === status, { status: response.status(), expected: status });
  return body;
};
const finalizeInUi = async page => {
  const response = page.waitForResponse(candidate => candidate.request().method() === 'POST' &&
    new URL(candidate.url()).pathname.endsWith('/finalize'));
  await page.getByRole('button', { name: 'Salvează și finalizează' }).click();
  return response;
};

try {
  const apiClient = await request.newContext();
  const suffix = `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
  const password = 'fictitious-browser-passphrase';
  const ownerResponse = await apiClient.post(`${api}/auth/register`, { data: {
    clinic_name: `Paro browser fictiv ${suffix}`, full_name: 'Medic proprietar fictiv',
    email: `paro-owner-${suffix}@example.com`, password,
  } });
  const ownerTokens = await expectStatus(ownerResponse, 201, 'create fictitious clinic owner');
  const ownerHeaders = { Authorization: `Bearer ${ownerTokens.access_token}` };
  const peerInviteResponse = await apiClient.post(`${api}/staff/invitations`, { headers: ownerHeaders, data: {
    email: `paro-peer-${suffix}@example.com`, full_name: 'Medic coleg fictiv', role: 'doctor',
  } });
  const invitation = await expectStatus(peerInviteResponse, 201, 'invite same-clinic clinical peer');
  const inviteToken = new URL(invitation.invitation_url, api).searchParams.get('token');
  const peerResponse = await apiClient.post(`${api}/staff/invitations/accept?token=${encodeURIComponent(inviteToken)}`, {
    data: { password },
  });
  const peer = await expectStatus(peerResponse, 200, 'accept same-clinic peer invitation');
  const peerLogin = await apiClient.post(`${api}/auth/login`, { data: { email: peer.email, password } });
  const peerTokens = await expectStatus(peerLogin, 200, 'login same-clinic peer');
  const peerHeaders = { Authorization: `Bearer ${peerTokens.access_token}` };

  const createPatient = async first_name => {
    const response = await apiClient.post(`${api}/patients`, { headers: ownerHeaders, data: { first_name, last_name: 'Paro fictiv' } });
    return (await expectStatus(response, 201, 'create fictitious patient')).id;
  };
  const patientA = await createPatient(`Pacient A ${suffix}`);
  const patientB = await createPatient(`Pacient B ${suffix}`);
  // T08 smoke after integration: create and read a structured plan via the real API.
  const planResponse = await apiClient.post(`${api}/treatment-plans/patients/${patientA}`, {
    headers: ownerHeaders, data: { title: `T08 smoke ${suffix}`, stages: [{ title: 'Etapă fictivă' }] },
  });
  const plan = await expectStatus(planResponse, 201, 'T08 plan creation smoke');
  const planRead = await apiClient.get(`${api}/treatment-plans/${plan.id}`, { headers: ownerHeaders });
  const planBody = await expectStatus(planRead, 200, 'T08 plan read smoke');
  check('T08 plan persisted', planBody.id === plan.id && planBody.title === plan.title, { planId: plan.id });

  const context = await browser.newContext();
  await context.addInitScript(({ access, refresh }) => {
    localStorage.setItem('access_token', access);
    localStorage.setItem('refresh_token', refresh);
  }, { access: ownerTokens.access_token, refresh: ownerTokens.refresh_token });
  const page = await context.newPage();
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  // Console errors and HTTP >= 400 are recorded (not asserted) so each can be classified as expected or defect.
  page.on('console', msg => { if (msg.type() === 'error') { let sourcePath = null; try { sourcePath = msg.location().url ? new URL(msg.location().url).pathname.replace(/[0-9a-f-]{36}/g, ':id') : null; } catch {} consoleErrors.push({ text: msg.text().slice(0, 200), sourcePath }); } });
  page.on('response', response => { if (response.status() >= 400) consoleErrors.push({ httpStatus: response.status(), method: response.request().method(), path: new URL(response.url()).pathname.replace(/[0-9a-f-]{36}/g, ':id') }); });
  await page.goto(`${web}/patients/${patientA}`);
  await page.getByRole('heading', { name: 'Evaluare parodontală' }).waitFor();
  await page.getByRole('heading', { name: 'Planuri de tratament' }).waitFor();
  // Integrated record (#20): panels mount after the clinic context; wait for the odontogram's own load before counting.
  await page.locator('.patient-odontogram').first().waitFor();
  const periodontalPanels = await page.locator('.periodontal-section').count();
  const odontograms = await page.locator('.patient-odontogram').count();
  check('one periodontal panel and one odontogram', periodontalPanels === 1 && odontograms === 1, { periodontalPanels, odontograms });
  await page.getByRole('heading', { name: plan.title }).waitFor();
  check('T08 smoke rendered in the integrated patient record', true, { planId: plan.id });

  await page.getByRole('button', { name: 'Evaluare nouă' }).click();
  const history = page.getByLabel('Istoric');
  await page.locator('.periodontal-editor').waitFor();
  const draft = { id: await history.inputValue() };
  check('draft created from the patient record', Boolean(draft.id), { draftId: draft.id });
  await page.getByRole('button', { name: 'Salvează și finalizează' }).click();
  await page.getByRole('alert').filter({ hasText: /\S/ }).waitFor();
  const retainedError = await page.getByRole('alert').filter({ hasText: /\S/ }).innerText();
  check('empty finalize error is controlled and draft remains selected', Boolean(retainedError) && await history.inputValue() === draft.id, { draftId: draft.id });
  await page.reload();
  await page.getByRole('heading', { name: 'Evaluare parodontală' }).waitFor();
  await page.getByLabel('Istoric').locator(`option[value="${draft.id}"]`).waitFor({ state: 'attached' });
  await page.getByLabel('Istoric').selectOption(draft.id);

  // The draft is private to its author, including within the same clinic.
  const peerRead = await apiClient.get(`${api}/periodontal/assessments/${draft.id}`, { headers: peerHeaders });
  await expectStatus(peerRead, 404, 'peer cannot read another user draft');
  const peerWrite = await apiClient.put(`${api}/periodontal/assessments/${draft.id}/measurements`, {
    headers: peerHeaders, data: { teeth: [] },
  });
  await expectStatus(peerWrite, 404, 'peer cannot write another user draft');
  const peerFinalize = await apiClient.post(`${api}/periodontal/assessments/${draft.id}/finalize`, { headers: peerHeaders });
  await expectStatus(peerFinalize, 404, 'peer cannot finalize another user draft');
  const peerList = await apiClient.get(`${api}/periodontal/patients/${patientA}/assessments`, { headers: peerHeaders });
  const peerRows = await expectStatus(peerList, 200, 'peer sees finalized history only');
  check('same-clinic peer draft is absent from history', !peerRows.some(row => row.id === draft.id), { peerVisibleDrafts: peerRows.length });

  const tooth = page.getByLabel('Dinte FDI');
  await tooth.selectOption('11');
  const site = page.locator('.periodontal-sites fieldset').first();
  await site.getByLabel('Adâncime (mm)').fill('0');
  await site.getByLabel('Recesiune (mm)').fill('-1.25');
  await site.getByLabel('BOP').selectOption('no');
  await site.getByLabel('Placă').selectOption('yes');
  await site.getByLabel('Supurație').selectOption('');
  await page.getByRole('button', { name: 'Salvează ciorna' }).click();
  await page.reload();
  await page.getByRole('heading', { name: 'Evaluare parodontală' }).waitFor();
  await page.getByLabel('Istoric').selectOption(draft.id);
  await tooth.selectOption('11');
  check('saved zero, false, and null survive reload',
    await page.locator('.periodontal-sites fieldset').first().getByLabel('Adâncime (mm)').inputValue() === '0' &&
    await page.locator('.periodontal-sites fieldset').first().getByLabel('BOP').inputValue() === 'no' &&
    await page.locator('.periodontal-sites fieldset').first().getByLabel('Supurație').inputValue() === '', { draftId: draft.id });
  const firstFinalize = await finalizeInUi(page);
  check('first assessment finalization response', firstFinalize.status() === 200, { status: firstFinalize.status() });
  await page.locator('.periodontal-history').waitFor();

  await page.getByRole('button', { name: 'Creează reevaluare ulterioară' }).click();
  await page.getByLabel('Dinte FDI').selectOption('11');
  await page.locator('.periodontal-sites fieldset').first().getByLabel('Adâncime (mm)').fill('1');
  const followupFinalize = await finalizeInUi(page);
  check('follow-up finalization response', followupFinalize.status() === 200, { status: followupFinalize.status() });
  await page.reload();
  await page.getByRole('heading', { name: 'Evaluare parodontală' }).waitFor();
  const historyResponse = await apiClient.get(`${api}/periodontal/patients/${patientA}/assessments`, { headers: ownerHeaders });
  const historyRows = await expectStatus(historyResponse, 200, 'read finalized assessment history');
  const finalizedIds = historyRows.filter(row => row.status === 'finalized').map(row => row.id);
  check('follow-up finalized as a separate persistent assessment', finalizedIds.length === 2, { finalizedCount: finalizedIds.length });
  await page.getByLabel('Istoric').locator(`option[value="${finalizedIds[0]}"]`).waitFor({ state: 'attached' });
  await page.getByLabel('Prima evaluare').selectOption(finalizedIds[1]);
  await page.getByLabel('A doua evaluare').selectOption(finalizedIds[0]);
  await page.getByRole('button', { name: 'Compară' }).click();
  const comparison = page.locator('.periodontal-comparison-results');
  await comparison.getByText(/BOP a doua evaluare:/).first().waitFor();
  check('comparison displays persisted measurement delta', (await comparison.innerText()).includes('Δ 1'), { finalizedCount: finalizedIds.length });
  await page.locator('.periodontal-section').screenshot({ path: join(evidenceDir, 'periodontal-comparison.png') });

  await page.goto(`${web}/patients/${patientB}`);
  await page.getByRole('heading', { name: `Pacient B ${suffix} Paro fictiv` }).waitFor();
  await page.getByText('Nu există evaluări parodontale.').waitFor();
  check('patient switch resets periodontal history', await page.locator('.periodontal-history').count() === 0, { patientB });
  check('no browser page errors', pageErrors.length === 0, { pageErrors });

  const safeChecks = checks.map(({ name, ok, status, expected, periodontalPanels, odontograms, peerVisibleDrafts, finalizedCount }) => ({
    name, ok, ...(status === undefined ? {} : { status }), ...(expected === undefined ? {} : { expected }),
    ...(periodontalPanels === undefined ? {} : { periodontalPanels }), ...(odontograms === undefined ? {} : { odontograms }),
    ...(peerVisibleDrafts === undefined ? {} : { peerVisibleDrafts }), ...(finalizedCount === undefined ? {} : { finalizedCount }),
  }));
  const evidence = { result: 'PASS', browser: browser.version(), checks: safeChecks, pageErrors: [], consoleErrors };
  await writeFile(join(evidenceDir, 'periodontal-acceptance-LATEST.json'), `${JSON.stringify(evidence, null, 2)}\n`);
  console.log(JSON.stringify({ result: evidence.result, browser: evidence.browser, checks: safeChecks.length, consoleErrors: consoleErrors.length, evidenceDir }));
  await context.close();
  await apiClient.dispose();
} finally {
  await browser.close();
}
