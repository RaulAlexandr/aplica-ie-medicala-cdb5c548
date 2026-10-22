#!/usr/bin/env node
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const runnerRoot = '/home/raul/Projects/dentacare-tools/browser-runner';
const browsersRoot = '/home/raul/Projects/dentacare-tools/browsers';
const executablePath = process.env.T06_CHROMIUM_EXECUTABLE
  ?? path.join(browsersRoot, 'chromium_headless_shell-1234/chrome-headless-shell-linux64/chrome-headless-shell');
const inputUrl = process.argv[2] ?? process.env.T06_DEMO_URL;
if (!inputUrl) {
  console.error('Usage: node scripts/t06-browser-check.mjs <http://127.0.0.1:5176/t06-demo.html>');
  process.exit(2);
}

const url = new URL(inputUrl);
if (!['127.0.0.1', 'localhost'].includes(url.hostname)) {
  throw new Error(`Only loopback URLs are allowed, received: ${url.origin}`);
}

const timestamp = new Date().toISOString().replaceAll(':', '-');
const resultsDir = process.env.T06_RESULTS_DIR
  ?? path.join(os.tmpdir(), 'dentacare-t06-browser-results', timestamp);
await mkdir(resultsDir, { recursive: true });
const require = createRequire(pathToFileURL(path.join(runnerRoot, 'package.json')));
const { chromium } = require('playwright');
const report = { url: url.toString(), executablePath, resultsDir, scenarios: [], consoleErrors: [], pageErrors: [], blockedExternalRequests: [] };
let browser;
let page;
let currentScenario = 'browser startup';

function scenario(name, result = 'passed') {
  report.scenarios.push({ name, result });
  console.log(`PASS ${name}`);
}

async function selectTooth(pageRef, fdi, status = 'prezent') {
  await pageRef.getByRole('button', { name: `Dinte ${fdi}, ${status}` }).click();
  await assert.equal(await pageRef.locator('.og-detail-heading h2').innerText(), `${fdi} · FDI ${fdi.replace('.', '')}`);
}

async function addFinding(pageRef, description, surfaces = []) {
  await pageRef.getByLabel('Descriere', { exact: true }).fill(description);
  for (const surfaceLabel of surfaces) await pageRef.getByRole('button', { name: surfaceLabel }).first().click();
  await pageRef.getByRole('button', { name: 'Adaugă constatarea' }).click();
  await pageRef.locator('.og-finding').filter({ hasText: description }).waitFor({ state: 'visible' });
}

try {
  browser = await chromium.launch({ headless: true, executablePath, timeout: 10_000, args: ['--no-sandbox'] });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, serviceWorkers: 'block' });
  page = await context.newPage();
  page.setDefaultTimeout(5_000);
  page.setDefaultNavigationTimeout(10_000);
  page.on('console', (message) => { if (message.type() === 'error') report.consoleErrors.push(message.text()); });
  page.on('pageerror', (error) => report.pageErrors.push(error.message));
  await page.route('**/*', async (route) => {
    const requestUrl = new URL(route.request().url());
    if (requestUrl.origin !== url.origin) {
      report.blockedExternalRequests.push(requestUrl.origin);
      await route.abort();
    } else {
      await route.continue();
    }
  });

  currentScenario = 'A: initial selection and independent 1.6 → 1.7 → 1.6 finding flow';
  await page.goto(url.toString(), { waitUntil: 'networkidle' });
  await assert.equal(await page.locator('.og-demo-banner').innerText(), 'Demonstrație — date locale, fără salvare pe server.');
  await assert.equal(await page.locator('button[aria-pressed="true"][data-tooth-id="16"]').count(), 1);
  await assert.equal(await page.locator('.og-finding').filter({ hasText: 'Restaurare veche, margini de urmărit la control.' }).count(), 1);
  await page.screenshot({ path: path.join(resultsDir, 't06-desktop-initial.png'), fullPage: true });
  await selectTooth(page, '1.7');
  await addFinding(page, 'Carie ocluzală observată în demonstrație.', ['Ocluzală (occlusal)', 'Mezială (mesial)']);
  await assert.equal(await page.locator('.og-finding-meta').filter({ hasText: 'occlusal, mesial' }).count(), 1);
  await page.screenshot({ path: path.join(resultsDir, 't06-desktop-finding-added.png'), fullPage: true });
  await selectTooth(page, '1.6');
  await assert.equal(await page.locator('.og-finding').filter({ hasText: 'Restaurare veche, margini de urmărit la control.' }).count(), 1);
  await assert.equal(await page.locator('.og-finding').filter({ hasText: 'Carie ocluzală observată în demonstrație.' }).count(), 0);
  await selectTooth(page, '1.7');
  await assert.equal(await page.locator('.og-finding').filter({ hasText: 'Carie ocluzală observată în demonstrație.' }).count(), 1);
  assert.equal(await page.getByRole('button', { name: /timer|tarif|manoperă/i }).count(), 0);
  scenario('A: confirmare 1.6/1.7, suprafețe și lipsa controalelor de tarif/timer/manoperă');

  currentScenario = 'B: tooth-specific draft state';
  await page.reload({ waitUntil: 'networkidle' });
  await page.getByLabel('Descriere', { exact: true }).fill('Ciornă fără salvare pentru 1.6.');
  await page.getByRole('button', { name: 'Distală (distal)' }).first().click();
  await selectTooth(page, '1.7');
  await assert.equal(await page.getByLabel('Descriere', { exact: true }).inputValue(), '');
  await page.getByLabel('Descriere', { exact: true }).fill('Ciornă fără salvare pentru 1.7.');
  await page.getByRole('button', { name: 'Ocluzală (occlusal)' }).first().click();
  await selectTooth(page, '1.6');
  await assert.equal(await page.getByLabel('Descriere', { exact: true }).inputValue(), 'Ciornă fără salvare pentru 1.6.');
  await assert.equal(await page.getByRole('button', { name: 'Distală (distal)' }).first().getAttribute('aria-pressed'), 'true');
  await selectTooth(page, '1.7');
  await assert.equal(await page.getByLabel('Descriere', { exact: true }).inputValue(), 'Ciornă fără salvare pentru 1.7.');
  await assert.equal(await page.getByRole('button', { name: 'Ocluzală (occlusal)' }).first().getAttribute('aria-pressed'), 'true');
  scenario('B: ciornele și suprafețele rămân asociate propriului dinte');

  currentScenario = 'C: correction and history';
  await page.reload({ waitUntil: 'networkidle' });
  await addFinding(page, 'A doua constatare care trebuie păstrată.', ['Distală (distal)']);
  await page.getByRole('button', { name: 'Corectează constatarea' }).first().click();
  await page.getByLabel('Descriere corectată pentru constatarea finding-demo-1601').fill('Restaurare verificată și corectată explicit.');
  await page.getByRole('button', { name: 'Salvează corecția' }).click();
  await page.getByText('Anterior: Obturație existentă: Restaurare veche, margini de urmărit la control.', { exact: true }).waitFor();
  await page.getByText('Valoare curentă: Obturație existentă: Restaurare verificată și corectată explicit.', { exact: true }).waitFor();
  await assert.equal(await page.locator('.og-finding').filter({ hasText: 'A doua constatare care trebuie păstrată.' }).count(), 1);
  await assert.equal(await page.locator('.og-finding').filter({ hasText: 'Restaurare verificată și corectată explicit.' }).count(), 1);
  scenario('C: corecția identificată păstrează valoarea veche/nouă și celelalte constatări');

  currentScenario = 'D: missing tooth selection';
  await page.getByRole('button', { name: 'Dinte 1.8, lipsă' }).click();
  await assert.equal(await page.locator('button[data-tooth-id="18"] .og-missing-mark').count(), 1);
  await assert.equal(await page.locator('.og-missing-note').innerText(), 'Dinte marcat lipsă. Poate fi selectat pentru consultarea istoricului.');
  await assert.equal(await page.getByRole('heading', { name: 'Istoric demonstrativ' }).count(), 1);
  await assert.equal(await page.locator('.og-history').innerText().then((text) => /Nu există evenimente|Constatare|Stare/.test(text)), true);
  scenario('D: dintele lipsă este distinct, selectabil și are panou de istoric');

  currentScenario = 'E: keyboard selection, focus and no trap';
  await page.reload({ waitUntil: 'networkidle' });
  await page.keyboard.press('Tab');
  await assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-tooth-id')), '18');
  await page.keyboard.press('Enter');
  await assert.equal(await page.locator('button[data-tooth-id="18"]').getAttribute('aria-pressed'), 'true');
  await page.keyboard.press('ArrowRight');
  await assert.equal(await page.evaluate(() => document.activeElement?.getAttribute('data-tooth-id')), '17');
  await assert.equal(await page.locator('button[data-tooth-id="17"]').getAttribute('aria-pressed'), 'true');
  const focusStyle = await page.evaluate(() => ({
    outlineStyle: getComputedStyle(document.activeElement).outlineStyle,
    outlineWidth: getComputedStyle(document.activeElement).outlineWidth,
  }));
  assert.notEqual(focusStyle.outlineStyle, 'none');
  assert.notEqual(focusStyle.outlineWidth, '0px');
  await page.locator('button[data-tooth-id="16"]').focus();
  await page.keyboard.press('Space');
  await assert.equal(await page.locator('button[data-tooth-id="16"]').getAttribute('aria-pressed'), 'true');
  await page.keyboard.press('Tab');
  await assert.notEqual(await page.evaluate(() => document.activeElement?.getAttribute('data-tooth-id')), '16');
  scenario('E: Tab, Enter, Space, săgeți, focus vizibil și Tab fără capcană');

  currentScenario = 'F: desktop and mobile layout';
  const viewportChecks = [];
  for (const width of [1440, 375, 320]) {
    await page.setViewportSize({ width, height: 900 });
    const descriptionBox = await page.getByLabel('Descriere', { exact: true }).boundingBox();
    assert.ok(descriptionBox, `Accessible description field is missing at ${width}px`);
    const metrics = await page.evaluate(() => {
      const rect = (selector) => {
        const element = document.querySelector(selector);
        if (!element) return null;
        const box = element.getBoundingClientRect();
        return { left: box.left, right: box.right, top: box.top, bottom: box.bottom, width: box.width, height: box.height };
      };
      const teeth = [...document.querySelectorAll('.og-tooth')].map((button) => {
        const box = button.getBoundingClientRect();
        return { id: button.dataset.toothId, left: box.left, right: box.right, width: box.width, scrollWidth: button.scrollWidth };
      });
      return {
        viewport: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        chart: rect('.og-chart'),
        selectedDetail: rect('.og-detail-heading'),
        submit: rect('.og-primary-button'),
        teeth,
      };
    });
    metrics.textarea = {
      left: descriptionBox.x,
      right: descriptionBox.x + descriptionBox.width,
      top: descriptionBox.y,
      bottom: descriptionBox.y + descriptionBox.height,
      width: descriptionBox.width,
      height: descriptionBox.height,
    };
    assert.ok(metrics.documentWidth <= width, `Horizontal overflow at ${width}px: ${metrics.documentWidth}px`);
    for (const selector of ['chart', 'selectedDetail', 'textarea', 'submit']) {
      const box = metrics[selector];
      assert.ok(box && box.left >= 0 && box.right <= width + 1, `${selector} clipped at ${width}px: ${JSON.stringify(box)}`);
    }
    assert.equal(metrics.teeth.length, 32);
    for (const tooth of metrics.teeth) {
      assert.ok(tooth.width > 0 && tooth.left >= 0 && tooth.right <= width + 1, `Tooth ${tooth.id} clipped at ${width}px`);
      assert.ok(tooth.left >= metrics.chart.left + 1 && tooth.right <= metrics.chart.right - 1, `Tooth ${tooth.id} clipped by odontogram panel at ${width}px`);
      assert.ok(tooth.scrollWidth <= tooth.width + 2, `Tooth ${tooth.id} label overflows its control at ${width}px`);
    }
    viewportChecks.push(metrics);
    if (width === 375 || width === 320) await page.screenshot({ path: path.join(resultsDir, `t06-mobile-${width}.png`), fullPage: true });
  }
  scenario('F: 1440px, 375px și 320px fără decupare sau overflow măsurabil');

  assert.deepEqual(report.consoleErrors, []);
  assert.deepEqual(report.pageErrors, []);
  assert.deepEqual(report.blockedExternalRequests, []);
  report.viewportChecks = viewportChecks;
  scenario('Console curat și fără cereri externe');
} catch (error) {
  report.failure = { scenario: currentScenario, message: error?.stack ?? String(error) };
  if (page) {
    try { await page.screenshot({ path: path.join(resultsDir, 't06-failure.png'), fullPage: true }); } catch {}
  }
  console.error(`FAIL ${currentScenario}: ${error?.message ?? error}`);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  await writeFile(path.join(resultsDir, 'report.json'), `${JSON.stringify(report, null, 2)}\n`);
  console.log(`Results: ${resultsDir}`);
}
