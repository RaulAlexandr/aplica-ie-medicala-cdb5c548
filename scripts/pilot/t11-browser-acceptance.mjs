// T11 browser acceptance against a running fictional pilot runtime (real API + SQLite + local
// protected storage). Uploads, association, zoom/comparison, reload, patient switch, archive and
// download go through the UI. Patient/visit setup and direct-access refusals use the HTTP API.
// Fictional files are generated here. Credentials are read locally and never written to output.
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
const evidenceDir = process.env.T11_EVIDENCE_DIR || path.join(process.env.DENTACARE_PILOT_HOME, 'browser-evidence');
const fixtureDir = path.join(process.env.DENTACARE_PILOT_HOME, 't11-fixtures');
await fs.mkdir(evidenceDir, { recursive: true, mode: 0o700 });
await fs.mkdir(fixtureDir, { recursive: true, mode: 0o700 });
const runId = new Date().toISOString().replaceAll(':', '-');
const tag = runId.slice(11, 19);
const summary = { task: 'T11', appSha: process.env.DENTACARE_APP_SHA, web, api, browser: null, checks: [], artifacts: [], network: {}, console: [], errors: [] };
const check = (name, ok, details = {}) => { summary.checks.push({ name, passed: Boolean(ok), ...details }); if (!ok) throw new Error(`Acceptance failed: ${name} ${JSON.stringify(details)}`); };
const shot = async (page, name) => { const file = path.join(evidenceDir, `t11-${name}.png`); await page.screenshot({ path: file, fullPage: true }); summary.artifacts.push(path.basename(file)); };

// ---- Fictional files ----
function crc32(buf) { let c, crc = 0xffffffff; for (const b of buf) { c = (crc ^ b) & 0xff; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; crc = (crc >>> 8) ^ c; } return (crc ^ 0xffffffff) >>> 0; }
function png(width, height, shade) {
  const chunk = (type, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]); const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(td)); return Buffer.concat([len, td, crc]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr[8] = 8; ihdr[9] = 0;
  const rows = [];
  for (let y = 0; y < height; y++) { const row = Buffer.alloc(width + 1); for (let x = 0; x < width; x++) row[x + 1] = ((x >> 5) + (y >> 5)) % 2 ? shade : 255 - shade; rows.push(row); }
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ihdr), chunk('IDAT', zlib.deflateSync(Buffer.concat(rows))), chunk('IEND', Buffer.alloc(0))]);
}
const pdf = Buffer.from('%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n2 0 obj << /Type /Pages /Kids [] /Count 0 >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n');
const files = {
  rx: { name: `radiografie-16-${tag}.png`, data: png(1600, 1000, 60) },
  photo: { name: `foto-intraoral-${tag}.png`, data: png(1200, 900, 120) },
  pdf: { name: `consimtamant-fictiv-${tag}.pdf`, data: pdf },
  svg: { name: `activ-${tag}.svg`, data: Buffer.from('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>') },
};
for (const file of Object.values(files)) { file.path = path.join(fixtureDir, file.name); await fs.writeFile(file.path, file.data, { mode: 0o600 }); }

async function apiSession(account) {
  const ctx = await request.newContext();
  const login = await ctx.post(`${api}/auth/login`, { data: { email: account.email, password: account.password } });
  if (!login.ok()) throw new Error(`API login failed for role ${account.role ?? '?'}: ${login.status()}`);
  const token = (await login.json()).access_token;
  const call = async (method, url, data, raw = false) => {
    const response = await ctx.fetch(`${api}${url}`, { method, data, headers: { Authorization: `Bearer ${token}` } });
    return { status: response.status(), headers: response.headers(), body: raw ? await response.body() : await response.json().catch(() => null) };
  };
  return { ctx, call };
}

const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE });
summary.browser = browser.version();
const uploads = { count: 0 };
const pageErrors = [];
async function login(account, label) {
  const context = await browser.newContext({ viewport: { width: 1360, height: 950 }, acceptDownloads: true });
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(String(error.message).slice(0, 300)));
  page.on('console', msg => { if (msg.type() === 'error') summary.console.push({ user: label, text: msg.text().slice(0, 160), url: (msg.location()?.url ?? '').replace(/[?#].*$/, '').replace(/[0-9a-f-]{36}/g, ':id') }); });
  page.on('request', req => { if (req.method() === 'POST' && /\/api\/documents\/patients\/[^/]+$/.test(new URL(req.url()).pathname)) uploads.count += 1; });
  await page.goto(web, { waitUntil: 'domcontentloaded' });
  await page.getByLabel('Email').fill(account.email);
  await page.getByLabel('Password').fill(account.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await page.getByRole('link', { name: 'Patients', exact: true }).waitFor();
  return { context, page };
}
const panelOf = page => page.getByRole('region', { name: 'Documente și imagini' });
async function openPatient(page, id) {
  await page.goto(`${web}/patients/${id}`, { waitUntil: 'domcontentloaded' });
  await panelOf(page).waitFor();
}
const itemOf = (panel, name) => panel.locator('article.doc-item', { hasText: name });

const doctorApi = await apiSession({ ...creds.doctor, role: 'doctor' });
const contexts = [];
try {
  // ---- Setup (existing T00-T02 APIs, fictional data) ----
  const me = (await doctorApi.call('GET', '/auth/me')).body;
  const rooms = (await doctorApi.call('GET', '/rooms')).body;
  const patientA = (await doctorApi.call('POST', '/patients', { first_name: 'Ilinca', last_name: `T11Fictiv${tag.replaceAll(':', '')}` })).body;
  const patientB = (await doctorApi.call('POST', '/patients', { first_name: 'Radu', last_name: `T11Altul${tag.replaceAll(':', '')}` })).body;
  // Random future weekday slots, retried on 409, so repeated runs on the same fictional DB do not
  // collide with earlier bookings or fall outside working hours.
  async function bookVisit(patientId, type) {
    let last = null;
    for (let attempt = 0; attempt < 25; attempt++) {
      const start = new Date(Date.now() + (2 + Math.floor(Math.random() * 180)) * 86400000);
      if ([0, 6].includes(start.getUTCDay())) continue;
      start.setUTCHours(7 + Math.floor(Math.random() * 6), Math.floor(Math.random() * 6) * 10, 0, 0);
      last = await doctorApi.call('POST', '/appointments', { patient_id: patientId, doctor_id: me.id, room_id: rooms[attempt % rooms.length].id, starts_at: start.toISOString(), duration_minutes: 30, appointment_type: type });
      if (last.status !== 409) return last;
    }
    return last;
  }
  const visit = await bookVisit(patientA.id, 'Control T11 fictiv');
  check('setup: fictional visit for patient A via T02 API', visit?.status === 201, { status: visit?.status, detail: visit?.body?.detail });
  const visitB = await bookVisit(patientB.id, 'Control B fictiv');
  check('setup: fictional visit for patient B', visitB?.status === 201, { status: visitB?.status, detail: visitB?.body?.detail });
  const docsOf = async patientId => (await doctorApi.call('GET', `/documents/patients/${patientId}?include_archived=true`)).body;

  // ---- Doctor: upload with visit + tooth, error keeps form, single save ----
  const doctor = await login(creds.doctor, 'doctor'); contexts.push(doctor.context);
  const page = doctor.page;
  await openPatient(page, patientA.id);
  const panel = panelOf(page);
  check('single T11 panel and single odontogram in patient record', await panel.count() === 1 && await page.locator('.patient-odontogram').count() === 1);
  const rules = await panel.getByTestId('document-rules').innerText();
  check('UI explains formats and limits', /JPEG și PNG/.test(rules) && /PDF/.test(rules) && /20\.0 MiB/.test(rules) && /DICOM/.test(rules) && /antivirus/.test(rules));

  await panel.getByLabel('Fișier').setInputFiles(files.svg.path);
  await panel.getByRole('button', { name: 'Salvează în dosar' }).click();
  await panel.getByTestId('document-form-error').waitFor();
  check('unsupported SVG refused in UI before upload', uploads.count === 0, { text: await panel.getByTestId('document-form-error').innerText() });

  await panel.getByLabel('Fișier').setInputFiles(files.rx.path);
  await panel.getByLabel('Categorie', { exact: true }).selectOption('radiograph');
  await panel.getByLabel('Vizită', { exact: true }).selectOption(visit.body.id);
  await panel.getByLabel('Dinte', { exact: true }).selectOption('16');
  await panel.getByLabel('Data clinică').fill('2026-11-18');
  await panel.getByLabel('Descriere').fill('Radiografie retroalveolară fictivă');
  let failed = false;
  await page.route('**/api/documents/patients/*', async route => {
    if (!failed && route.request().method() === 'POST') { failed = true; return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Stocare indisponibilă (simulat)' }) }); }
    return route.continue();
  });
  await panel.getByRole('button', { name: 'Salvează în dosar' }).click();
  await panel.getByText('Stocare indisponibilă (simulat)').waitFor();
  check('error keeps file and fields', (await panel.getByTestId('selected-file').innerText()).includes(files.rx.name)
    && await panel.getByLabel('Dinte', { exact: true }).inputValue() === '16'
    && await panel.getByLabel('Vizită', { exact: true }).inputValue() === visit.body.id
    && await panel.getByLabel('Descriere').inputValue() === 'Radiografie retroalveolară fictivă');
  await shot(page, 'form-error-keeps-draft');
  await page.unroute('**/api/documents/patients/*');
  const before = uploads.count;
  await panel.getByRole('button', { name: 'Salvează în dosar' }).dblclick();
  await panel.getByText('Fișier salvat în dosarul pacientului.').waitFor();
  let rows = await docsOf(patientA.id);
  check('repeated save creates exactly one document and one request', rows.length === 1 && uploads.count - before === 1, { documents: rows.length, uploadRequests: uploads.count - before });
  check('server stored visit, tooth, dimensions and distinct clinical/recorded dates', rows[0].appointment?.id === visit.body.id && rows[0].tooth_fdi === '16'
    && rows[0].width === 1600 && rows[0].height === 1000 && rows[0].clinical_date === '2026-11-18' && Boolean(rows[0].uploaded_at) && rows[0].original_filename === files.rx.name);

  await panel.getByLabel('Fișier').setInputFiles(files.photo.path);
  await panel.getByLabel('Categorie', { exact: true }).selectOption('photo');
  await panel.getByLabel('Dinte', { exact: true }).selectOption('16');
  await panel.getByRole('button', { name: 'Salvează în dosar' }).click();
  await panel.getByText('Fișier salvat în dosarul pacientului.').waitFor();
  await panel.getByLabel('Fișier').setInputFiles(files.pdf.path);
  await panel.getByLabel('Categorie', { exact: true }).selectOption('document');
  await panel.getByLabel('Vizită', { exact: true }).selectOption(visit.body.id);
  await panel.getByRole('button', { name: 'Salvează în dosar' }).click();
  await panel.getByText('Fișier salvat în dosarul pacientului.').waitFor();
  await itemOf(panel, files.pdf.name).waitFor();
  rows = await docsOf(patientA.id);
  check('image and PDF uploaded through the UI', rows.length === 3 && rows.filter(r => r.is_image).length === 2 && rows.some(r => r.content_type === 'application/pdf'));
  check('PDF has download but no in-app viewer', await itemOf(panel, files.pdf.name).getByRole('button', { name: 'Vizualizează' }).count() === 0
    && await itemOf(panel, files.pdf.name).getByRole('button', { name: 'Descarcă' }).count() === 1);
  await shot(page, 'documents-list');

  // ---- Zoom ----
  await itemOf(panel, files.rx.name).getByRole('button', { name: 'Vizualizează' }).click();
  const viewer = panel.getByLabel('Vizualizare imagine');
  const img = viewer.locator('img');
  await img.waitFor();
  await page.waitForFunction(() => { const el = document.querySelector('.doc-viewer img'); return el && el.complete && el.naturalWidth > 0; });
  const natural = await img.evaluate(el => [el.naturalWidth, el.naturalHeight]);
  check('authorized image rendered from blob, not from a public URL', (await img.getAttribute('src')).startsWith('blob:') && natural[0] === 1600 && natural[1] === 1000, { natural });
  await viewer.getByRole('button', { name: 'Mărește' }).click();
  await viewer.getByRole('button', { name: 'Mărește' }).click();
  const zoomed = await img.evaluate(el => el.getBoundingClientRect().width);
  check('zoom in enlarges the image', (await viewer.getByTestId('zoom-level').innerText()) === '156%' && Math.abs(zoomed - 1600 * 1.56) < 3, { zoomed });
  await shot(page, 'zoom-156');
  await viewer.getByRole('button', { name: 'Potrivește' }).click();
  const fitted = await img.evaluate(el => el.getBoundingClientRect().width);
  const stageWidth = await viewer.locator('.doc-stage').evaluate(el => el.clientWidth);
  check('fit zoom keeps the image inside the viewer', fitted <= stageWidth && fitted > 100, { fitted, stageWidth });

  // ---- Comparison ----
  await panel.getByLabel(`Compară ${files.rx.name}`).check();
  await panel.getByLabel(`Compară ${files.photo.name}`).check();
  await panel.getByRole('button', { name: 'Compară imaginile' }).click();
  const compare = panel.getByLabel('Comparație imagini');
  const panes = compare.getByTestId('compare-pane');
  await page.waitForFunction(() => [...document.querySelectorAll('.doc-compare img')].filter(el => el.complete && el.naturalWidth > 0).length === 2);
  check('two images side by side', await panes.count() === 2);
  await panes.nth(0).getByRole('button', { name: 'Mărește' }).click();
  check('synchronized zoom applies to both images', (await compare.getByTestId('zoom-level').allInnerTexts()).join(',') === '125%,125%');
  await compare.getByLabel('Zoom sincronizat').uncheck();
  await panes.nth(1).getByRole('button', { name: 'Mărește' }).click();
  check('independent zoom when unsynchronized', (await compare.getByTestId('zoom-level').allInnerTexts()).join(',') === '125%,156%');
  await shot(page, 'compare-two-images');

  // ---- Download PDF through the UI ----
  const downloadPromise = page.waitForEvent('download');
  await itemOf(panel, files.pdf.name).getByRole('button', { name: 'Descarcă' }).click();
  const download = await downloadPromise;
  const downloaded = await fs.readFile(await download.path());
  check('PDF downloaded with original name and identical bytes', download.suggestedFilename() === files.pdf.name && downloaded.equals(files.pdf.data));

  // ---- Reload persistence ----
  await page.reload({ waitUntil: 'domcontentloaded' });
  await itemOf(panelOf(page), files.rx.name).waitFor();
  const rxText = await itemOf(panelOf(page), files.rx.name).innerText();
  check('documents and associations persist after reload', await panelOf(page).locator('article.doc-item').count() === 3
    && rxText.includes('Dinte 16') && rxText.includes('Control T11 fictiv') && rxText.includes('Data clinică: 2026-11-18'));

  // ---- Patient switch (SPA navigation) ----
  await panelOf(page).getByLabel('Fișier').setInputFiles(files.photo.path);
  await panelOf(page).getByLabel('Dinte', { exact: true }).selectOption('36');
  await panelOf(page).getByLabel('Vizită', { exact: true }).selectOption(visit.body.id);
  await page.getByRole('link', { name: 'Patients', exact: true }).click();
  await page.getByPlaceholder('Name, phone or email').fill(patientB.last_name);
  await page.getByRole('link', { name: `${patientB.first_name} ${patientB.last_name}` }).click();
  await page.getByRole('heading', { level: 1, name: `${patientB.first_name} ${patientB.last_name}` }).waitFor();
  const panelB = panelOf(page);
  await panelB.getByText('Niciun document pentru filtrele alese.').waitFor();
  const visitValuesB = await panelB.getByLabel('Vizită', { exact: true }).locator('option').evaluateAll(options => options.map(o => o.value));
  check('patient switch carries no file, visit, tooth or documents', await panelB.getByTestId('selected-file').count() === 0
    && await panelB.getByLabel('Dinte', { exact: true }).inputValue() === '' && await panelB.getByLabel('Vizită', { exact: true }).inputValue() === ''
    && await panelB.getByRole('button', { name: 'Salvează în dosar' }).isDisabled()
    && !visitValuesB.includes(visit.body.id) && visitValuesB.includes(visitB.body.id), { visitValuesB });
  const foreign = await doctorApi.call('POST', `/documents/patients/${patientB.id}?category=photo&appointment_id=${visit.body.id}`, files.photo.data);
  check('API refuses associating patient A visit to patient B', foreign.status === 422, { status: foreign.status });
  await shot(page, 'patient-b-clean');

  // ---- Another authorized user (manager) ----
  const manager = await login(creds.manager, 'manager'); contexts.push(manager.context);
  await openPatient(manager.page, patientA.id);
  await itemOf(panelOf(manager.page), files.photo.name).waitFor();
  check('manager sees the same three documents', await panelOf(manager.page).locator('article.doc-item').count() === 3);
  await itemOf(panelOf(manager.page), files.photo.name).getByRole('button', { name: 'Vizualizează' }).click();
  await manager.page.waitForFunction(() => { const el = document.querySelector('.doc-viewer img'); return el && el.complete && el.naturalWidth === 1200; });
  check('manager views image content', true);
  await itemOf(panelOf(manager.page), files.photo.name).getByRole('button', { name: 'Arhivează' }).click();
  await panelOf(manager.page).getByLabel('Motiv arhivare').fill('Duplicat fictiv');
  await panelOf(manager.page).getByRole('button', { name: 'Confirmă arhivarea' }).click();
  await itemOf(panelOf(manager.page), files.photo.name).waitFor({ state: 'detached' });
  await panelOf(manager.page).getByLabel('Arată arhivate').check();
  const archivedText = await itemOf(panelOf(manager.page), files.photo.name).innerText();
  check('archive keeps metadata and traceability', archivedText.includes('ARHIVAT') && archivedText.includes('Duplicat fictiv'));
  await shot(manager.page, 'manager-archived');

  // ---- Unauthorized role: UI and direct content access ----
  const reception = await login(creds.reception, 'reception'); contexts.push(reception.context);
  await reception.page.goto(`${web}/patients/${patientA.id}`, { waitUntil: 'domcontentloaded' });
  await reception.page.getByRole('heading', { level: 1 }).waitFor();
  await reception.page.waitForTimeout(800);
  check('reception sees no T11 panel', await panelOf(reception.page).count() === 0);
  const receptionApi = await apiSession({ ...creds.reception, role: 'reception' });
  const docId = rows.find(r => r.original_filename === files.rx.name).id;
  const rList = await receptionApi.call('GET', `/documents/patients/${patientA.id}`);
  const rContent = await receptionApi.call('GET', `/documents/${docId}/content`, undefined, true);
  const rUpload = await receptionApi.call('POST', `/documents/patients/${patientA.id}?category=photo`, files.photo.data);
  check('reception gets 403 on list, direct content and upload', rList.status === 403 && rContent.status === 403 && rUpload.status === 403,
    { list: rList.status, content: rContent.status, upload: rUpload.status });
  const tab = await reception.context.newPage();
  const direct = await tab.goto(`${api}/documents/${docId}/content`);
  check('known content URL opened directly in the browser is refused (no auth by URL)', direct.status() === 401, { status: direct.status() });
  await tab.close();
  await receptionApi.ctx.dispose();
  const authorized = await doctorApi.call('GET', `/documents/${docId}/content`, undefined, true);
  check('authorized content has protective headers', authorized.status === 200 && authorized.headers['x-content-type-options'] === 'nosniff'
    && /no-store/.test(authorized.headers['cache-control']) && /sandbox/.test(authorized.headers['content-security-policy'])
    && Buffer.from(authorized.body).equals(files.rx.data));

  summary.network.documentUploadPostsFromUi = uploads.count;
  summary.pageErrors = pageErrors;
  check('no page errors', pageErrors.length === 0, { pageErrors });
  summary.result = 'PASS';
} catch (error) {
  summary.result = 'FAIL';
  summary.errors.push(String(error.message ?? error).slice(0, 800));
  process.exitCode = 1;
} finally {
  for (const context of contexts) await context.close().catch(() => {});
  await doctorApi.ctx.dispose();
  await browser.close();
  const passed = summary.checks.filter(c => c.passed).length;
  summary.totals = { passed, total: summary.checks.length };
  await fs.writeFile(path.join(evidenceDir, 't11-browser-summary.json'), JSON.stringify(summary, null, 2), { mode: 0o600 });
  console.log(`T11 browser ${summary.result}: ${passed}/${summary.checks.length} checks · evidence ${evidenceDir}`);
  if (summary.errors.length) console.log(summary.errors.join('\n'));
}
