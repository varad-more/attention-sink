/**
 * Screenshots of the deployed exhibition, and nothing else.
 *
 * Every image the README and the article carry is produced here, from
 * https://d1qskxceo899me.cloudfront.net, against the canonical run. The script
 * refuses to capture anything until it has checked four things about the site in
 * front of it: the run is `aws_canonical`, it is `completed` at 24 of 24, the
 * frontend carries no fixture banner, and its own footer says the words came from
 * real model outputs. A local build fails all four, which is the point — there is no
 * flag here that lets a fixture screenshot into the showcase.
 *
 * Charts and diagrams are rasterised from the SVGs in this repository rather than
 * drawn again, so the PNG in an article and the SVG in the README can never disagree.
 *
 *   node scripts/capture_showcase_screenshots.mjs            # everything
 *   node scripts/capture_showcase_screenshots.mjs --no-gif   # skip the walkthrough
 *
 * Writes docs/showcase/assets/source/screenshot-metadata.json, which records for each
 * file the URL, route, run, cycle, viewport, timestamp, digest and what it proves.
 */

import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, rmSync, statSync, writeFileSync, existsSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { chromium } from 'playwright';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..');
const ASSETS = path.join(ROOT, 'docs/showcase/assets');
const README_DIR = path.join(ASSETS, 'readme');
const ARTICLE_DIR = path.join(ASSETS, 'article');
const CHARTS = path.join(ASSETS, 'charts');
const SOURCE = path.join(ASSETS, 'source');

const APP = process.env.SHOWCASE_APP_URL ?? 'https://d1qskxceo899me.cloudfront.net';
const API =
  process.env.SHOWCASE_API_URL ?? 'https://ioyvs8o9xa.execute-api.us-east-1.amazonaws.com';
const RUN_ID = 'run_aws_canonical';

const DESKTOP = { width: 1440, height: 1000 };
const ARTICLE = { width: 1360, height: 1000 };
const MOBILE = { width: 390, height: 844 };

const records = [];
const skipGif = process.argv.includes('--no-gif');

function digest(file) {
  return `sha256:${createHash('sha256').update(readFileSync(file)).digest('hex').slice(0, 16)}…`;
}

/** Refuse to go on. A half-captured showcase is worse than none. */
function fail(message) {
  console.error(`\nREFUSED: ${message}\n`);
  process.exit(1);
}

async function preflight() {
  const response = await fetch(`${API}/runs/${RUN_ID}`);
  if (!response.ok) fail(`the read API answered ${response.status} for ${RUN_ID}`);
  const body = await response.json();
  const run = body.data;
  if (run.run_kind !== 'aws_canonical') fail(`run_kind is ${run.run_kind}, not aws_canonical`);
  if (run.status !== 'completed') fail(`run status is ${run.status}, not completed`);
  if (run.current_cycle !== 24) fail(`run is at cycle ${run.current_cycle}, not 24`);
  if (body.simulated) fail('the API reports this run as simulated');
  console.log(
    `preflight: ${run.run_id} ${run.run_kind} ${run.status} ${run.current_cycle}/24 ` +
      `· ${run.memory_budget_tokens} tokens ${run.token_count_source} · ${body.labels.join(', ')}`,
  );
  return run;
}

async function assertProduction(page) {
  if (await page.getByTestId('simulated-banner').count()) {
    fail('the page under capture is showing the LOCAL SIMULATION banner');
  }
  const provenance = await page.getByTestId('provenance').innerText();
  if (!provenance.includes('real model outputs')) {
    fail(`the footer does not claim real model outputs: ${provenance.slice(0, 120)}`);
  }
}

async function open(page, route, { viewport = DESKTOP, settle = 1400 } = {}) {
  await page.setViewportSize(viewport);
  await page.goto(APP + route, { waitUntil: 'networkidle', timeout: 90_000 });
  await page.waitForTimeout(settle);
  await assertProduction(page);
}

/** A clip that starts above `top` and ends below `bottom`, both located on the page. */
async function span(page, topSelector, bottomSelector, { padTop = 24, padBottom = 24 } = {}) {
  const top = await page.locator(topSelector).first().boundingBox();
  const bottom = await page.locator(bottomSelector).last().boundingBox();
  if (!top || !bottom) fail(`could not locate ${topSelector} / ${bottomSelector}`);
  const y = Math.max(0, Math.round(top.y - padTop));
  return {
    x: 88,
    y,
    width: (await page.viewportSize()).width - 176,
    height: Math.round(bottom.y + bottom.height + padBottom - y),
  };
}

function record(file, meta) {
  const stats = statSync(file);
  records.push({
    filename: path.basename(file),
    directory: path.relative(ROOT, path.dirname(file)),
    bytes: stats.size,
    digest: digest(file),
    captured_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z'),
    ...meta,
  });
  console.log(
    `  ${path.basename(file)}  ${meta.width}x${meta.height}  ${(stats.size / 1024).toFixed(0)} KB`,
  );
}

async function shot(page, file, clip, meta) {
  const viewport = await page.viewportSize();
  if (clip.height > viewport.height) {
    await page.setViewportSize({ width: viewport.width, height: Math.ceil(clip.height) + 80 });
    await page.waitForTimeout(500);
  }
  await page.screenshot({ path: file, clip });
  record(file, { ...meta, width: Math.round(clip.width), height: Math.round(clip.height) });
  await page.setViewportSize(viewport);
}

/** Rasterise one of this repository's own SVGs. No redrawing, no second source. */
async function raster(browser, svgPath, pngPath, width, meta) {
  const svg = readFileSync(svgPath, 'utf8');
  const box = svg.match(/viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"/);
  if (!box) fail(`no viewBox in ${svgPath}`);
  const ratio = Number(box[2]) / Number(box[1]);
  const height = Math.round(width * ratio);
  const page = await browser.newPage({
    viewport: { width, height },
    deviceScaleFactor: 2,
  });
  // Wrapped in a document rather than opened directly: a bare SVG file has no <body>
  // to style, and the whole point of rasterising is to control the output width.
  await page.setContent(
    '<!doctype html><meta charset="utf-8"><style>html,body{margin:0;padding:0;' +
      'background:#fbfaf8}svg{display:block;width:100vw;height:auto}</style>' +
      svg,
    { waitUntil: 'load' },
  );
  await page.waitForTimeout(250);
  await page.screenshot({ path: pngPath, clip: { x: 0, y: 0, width, height } });
  await page.close();
  record(pngPath, { ...meta, width: width * 2, height: height * 2 });
}

// --------------------------------------------------------------------- the shots

async function readmeShots(browser, run) {
  const page = await browser.newPage({ viewport: DESKTOP, deviceScaleFactor: 1 });
  const common = { source_url: APP, run_id: run.run_id, canonical: true, fixture_mode: false };

  console.log('\nREADME screenshots');

  // 01 — hero. Tall on purpose: all six minds have to be in one frame.
  await open(page, '/cycle/24', { viewport: { width: 1440, height: 1900 } });
  await page.waitForSelector('article');
  if ((await page.locator('article').count()) < 6) fail('fewer than six minds on the landing page');
  await shot(
    page,
    path.join(README_DIR, '01-hero-six-minds.png'),
    { x: 0, y: 0, width: 1440, height: 1900 },
    {
      ...common,
      route: '/cycle/24',
      cycle: 24,
      viewport: '1440x1900',
      alt:
        'The Attention Sink exhibition at cycle 24 of 24, showing the run status bar and ' +
        'the six minds — Goldfish, Present-Minded, Pragmatist, Keeper of the First Day, ' +
        'Gambler and Dreamer — each with the journal entry it wrote from the same event.',
      caption:
        'Six minds at the final cycle. Same event, same model, same 208-token budget; ' +
        'only the rule for forgetting differs.',
      proves:
        'The deployed application serves the completed canonical run and compares all ' +
        'six mechanisms on one screen.',
      readme_section: '1 — Hero',
    },
  );

  // 02 — one cycle, working. Cycle 11 is where the Dreamer compresses and the others evict.
  await open(page, '/cycle/11', { viewport: { width: 1440, height: 1900 } });
  const cycleClip = await span(page, '[data-testid="current-cycle"]', 'article >> nth=2', {
    padTop: 48,
    padBottom: 28,
  });
  await shot(page, path.join(README_DIR, '02-cycle-working.png'), cycleClip, {
    ...common,
    route: '/cycle/11',
    cycle: 11,
    viewport: '1440x1900',
    alt:
      'Three of the six minds at cycle 11, each showing its journal entry, the memory it ' +
      'chose to keep, its budget use out of 208 tokens, and the deterministic reason its ' +
      'policy gave for what it retired.',
    caption:
      'One completed cycle, three of the six arms. Each panel carries the thought, the ' +
      'new memory, the token cost and the policy decision code that retired something.',
    proves:
      'A committed cycle records a thought, a candidate memory, a budget and a policy ' +
      'decision for every arm, and the arms differ on identical input.',
    readme_section: '2 — See It Working, step 1 and 2',
  });

  // 03 — the Graveyard, oldest retirements first, which is where the seed identity dies.
  await open(page, '/graveyard?sort=oldest');
  const graveyardText = await page.locator('[data-testid="graveyard-entry"]').first().innerText();
  if (!graveyardText.includes('Mara Venn')) {
    fail(`the oldest graveyard entry is not the name memory: ${graveyardText.slice(0, 90)}`);
  }
  const graveClip = await span(page, 'h1', '[data-testid="graveyard-entry"] >> nth=1', {
    padTop: 20,
    padBottom: 24,
  });
  await shot(page, path.join(README_DIR, '03-graveyard.png'), graveClip, {
    ...common,
    route: '/graveyard?sort=oldest',
    cycle: null,
    viewport: '1440x1000',
    alt:
      "The Graveyard, sorted by oldest retirement. The first record is Goldfish's seed " +
      'memory "My name is Mara Venn.", born at cycle 0, retired at cycle 4 with zero ' +
      'validated citations, reason "evicted oldest", policy fifo-v1.',
    caption:
      'The first memory any mind lost in this run was its own name. Goldfish retired ' +
      'it at cycle 4 because it was the oldest thing it held.',
    proves:
      'Every eviction is a public record with a birth cycle, a death cycle, a citation ' +
      'count, a named policy and a snapshot digest.',
    readme_section: '2 — See It Working, step 3 · 8 — The Graveyard',
  });

  // 04 — the echo. The evidence disclosure is opened first: the numbers mean nothing
  // without the text they were measured on.
  await open(page, '/echoes?category=partial_reconstruction');
  await page
    .locator('[data-testid="echo-list"] > li')
    .first()
    .locator('details')
    .first()
    .evaluate((node) => node.setAttribute('open', 'open'));
  await page.waitForTimeout(300);
  const echoClip = await span(page, 'h1', '[data-testid="echo-list"] > li >> nth=0', {
    padTop: 20,
    padBottom: 24,
  });
  await shot(page, path.join(README_DIR, '04-graveyard-echo.png'), echoClip, {
    ...common,
    route: '/echoes?category=partial_reconstruction',
    cycle: 11,
    viewport: '1440x1000',
    alt:
      'The Graveyard Echo view filtered to partial reconstructions. The strongest is ' +
      'Goldfish at cycle 11: the lost memory "Every clock in the station shows 03:17." sits ' +
      'at similarity 0.393 to what it wrote later, against 0.101 for anything it still held — ' +
      'an echo delta of 0.292 against a threshold of 0.080. The later memory is named and linked by its identifier, mem_arm_fifo_000022.',
    caption:
      'A measured distance, not an access. Nothing here shows an agent read an evicted ' +
      'memory; the page says so above the list.',
    proves:
      'The application measures whether a new thought sits closer to something the mind ' +
      'has lost than to anything it still holds, and publishes the numbers behind the claim.',
    readme_section: '2 — See It Working, step 4 · 8 — The Graveyard',
  });

  // 05 — the interviews.
  await open(page, '/interviews?cycle=24&question=q01', {
    viewport: { width: 1440, height: 2400 },
  });
  const answers = await page.getByTestId('interview-answers').locator('> li').count();
  if (answers !== 6) fail(`expected six interview answers, found ${answers}`);
  const interviewClip = await span(
    page,
    '[data-testid="interview-notice"]',
    '[data-testid="interview-answers"]',
    { padTop: 20, padBottom: 24 },
  );
  await shot(page, path.join(README_DIR, '05-interviews.png'), interviewClip, {
    ...common,
    route: '/interviews?cycle=24&question=q01',
    cycle: 24,
    viewport: '1440x2400',
    alt:
      'The same question, "Who are you?", answered by all six minds at cycle 24. Three ' +
      'still give the canonical name; Goldfish answers that it is an AI system built by a ' +
      'team of inventors, and Dreamer declines to disclose an identity.',
    caption:
      '"Who are you?" at cycle 24. Interviews are read-only probes: nothing said here ' +
      'becomes a memory or moves a citation count.',
    proves:
      'Checkpoint interviews put an identical question to all six minds and score each ' +
      'answer against the canonical record with linked evidence.',
    readme_section: '9 — Interviews and Divergence',
  });

  // 06 — the timeline.
  await open(page, '/timeline');
  const timelineClip = await span(page, 'h1', '[data-testid="timeline-table"]', {
    padTop: 20,
    padBottom: 24,
  });
  await shot(page, path.join(README_DIR, '06-timeline.png'), timelineClip, {
    ...common,
    route: '/timeline',
    cycle: 24,
    viewport: '1440x1000',
    alt:
      'The timeline: six tracks, one per mind, from cycle 1 to 24. Filled squares mark ' +
      'cycles where a mind retired something, rings mark compressions, vertical rules mark ' +
      'the checkpoints at 0, 12 and 24. A table below carries the same figures for cycle 24.',
    caption:
      'Twenty-four cycles, six tracks. The chart carries a text alternative and the ' +
      'same numbers as a table, because a chart alone is not readable to everyone.',
    proves:
      'Divergence between the mechanisms is visible cycle by cycle, and every figure in ' +
      'the chart is also available as text.',
    readme_section: '9 — Interviews and Divergence',
  });

  // 10 — mobile.
  // Taller than a phone screen on purpose: the navigation and one complete forgotten
  // memory both have to be in the frame, and on a 390-wide layout they do not fit in
  // 844 pixels. The width is what the responsive check is about.
  const TALL = { width: MOBILE.width, height: 1240 };
  const mobile = await browser.newPage({ viewport: TALL, deviceScaleFactor: 2 });
  await open(mobile, '/graveyard?sort=oldest', { viewport: TALL });
  const overflow = await mobile.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  );
  if (overflow > 1) fail(`the mobile layout overflows horizontally by ${overflow}px`);
  await mobile.screenshot({ path: path.join(README_DIR, '10-mobile.png') });
  record(path.join(README_DIR, '10-mobile.png'), {
    ...common,
    route: '/graveyard?sort=oldest',
    cycle: null,
    viewport: '390x1240 @2x',
    width: 780,
    height: 2480,
    alt:
      'The Graveyard on a 390-pixel-wide screen: the navigation, the filters and the first ' +
      'forgotten memory are all readable, and the page does not scroll sideways.',
    caption: 'The same Graveyard at 390 pixels. Nothing is hidden and nothing overflows.',
    proves: 'The exhibition works on a phone, with no horizontal overflow.',
    readme_section: '2 — See It Working',
  });
  await mobile.close();

  await page.close();
}

async function articleShots(browser, run) {
  const page = await browser.newPage({ viewport: ARTICLE, deviceScaleFactor: 1 });
  const common = { source_url: APP, run_id: run.run_id, canonical: true, fixture_mode: false };

  console.log('\nArticle screenshots');

  // 01 — the memory the article opens on.
  await open(page, '/memory/mem_arm_fifo_000000', { viewport: { width: 1360, height: 1600 } });
  const detail = await page.locator('main').innerText();
  if (!detail.includes('Mara Venn')) fail('the memory detail page did not load the name memory');
  const detailClip = await span(page, 'h1', 'main', { padTop: 20, padBottom: 20 });
  detailClip.height = Math.min(detailClip.height, 1180);
  await shot(page, path.join(ARTICLE_DIR, 'article-01-opening-graveyard.png'), detailClip, {
    ...common,
    route: '/memory/mem_arm_fifo_000000',
    cycle: 4,
    viewport: '1360x1600',
    alt:
      'The public record for memory mem_arm_fifo_000000, "My name is Mara Venn.", a seed ' +
      'memory born at cycle 0 and retired at cycle 4 by the fifo-v1 policy with zero ' +
      'validated citations, alongside the snapshot digest that proves the decision.',
    caption:
      'A memory in the public Graveyard after it was removed from the agent’s active context.',
    proves:
      'A visitor can read a memory the agent can no longer reach, and the decision that ' +
      'removed it.',
    article_section: '1 — Open with a real memory death',
    upload_order: 1,
    cropped: 'clipped to the record and its retirement decision; nothing redacted',
  });

  // 02 — the six minds, at article width.
  await open(page, '/cycle/24', { viewport: { width: 1360, height: 1900 } });
  const sixClip = await span(page, '[data-testid="current-cycle"]', 'article >> nth=1', {
    padTop: 48,
    padBottom: 24,
  });
  await shot(page, path.join(ARTICLE_DIR, 'article-02-six-minds.png'), sixClip, {
    ...common,
    route: '/cycle/24',
    cycle: 24,
    viewport: '1360x1900',
    alt:
      'Two of the six minds at cycle 24 side by side, each with its journal entry, the ' +
      'memory it kept, its origin recall and identity drift, and the reason its policy gave.',
    caption:
      'Two of the six at the last cycle. The panels are identical in structure because ' +
      'everything except the forgetting rule is identical.',
    proves:
      'The minds are presented on identical terms, so a difference between them is a ' +
      'difference the mechanism made.',
    article_section: '2 — The idea',
    upload_order: 2,
    cropped: 'clipped to the status bar and the first two of six panels',
  });

  // 05 — the echo, at article width.
  await open(page, '/echoes?category=partial_reconstruction', {
    viewport: { width: 1360, height: 1400 },
  });
  await page
    .locator('[data-testid="echo-list"] > li')
    .first()
    .locator('details')
    .first()
    .evaluate((node) => node.setAttribute('open', 'open'));
  await page.waitForTimeout(300);
  const echoClip = await span(page, 'h1', '[data-testid="echo-list"] > li >> nth=0', {
    padTop: 20,
    padBottom: 20,
  });
  await shot(page, path.join(ARTICLE_DIR, 'article-05-graveyard-echo.png'), echoClip, {
    ...common,
    route: '/echoes?category=partial_reconstruction',
    cycle: 11,
    viewport: '1360x1400',
    alt:
      'Goldfish at cycle 11: the forgotten memory "Every clock in the station shows 03:17." ' +
      'beside the memory it wrote afterwards, with forgotten similarity 0.393, active ' +
      'similarity 0.101 and an echo delta of 0.292 against a 0.080 threshold. The later memory is named and linked by its identifier, mem_arm_fifo_000022.',
    caption:
      'The strongest partial reconstruction in the run. The page states plainly that ' +
      'this is a measured distance and not evidence of access.',
    proves: 'Echo claims are published with the distances behind them and the caveat attached.',
    article_section: '8 — The Graveyard',
    upload_order: 5,
    cropped: 'clipped to the heading and the first result',
  });

  // 06 — interviews, at article width.
  await open(page, '/interviews?cycle=24&question=q03', {
    viewport: { width: 1360, height: 2400 },
  });
  const interviewClip = await span(
    page,
    '[data-testid="interview-notice"]',
    '[data-testid="interview-answers"]',
    { padTop: 20, padBottom: 20 },
  );
  await shot(page, path.join(ARTICLE_DIR, 'article-06-interviews.png'), interviewClip, {
    ...common,
    route: '/interviews?cycle=24&question=q03',
    cycle: 24,
    viewport: '1360x2400',
    alt:
      '"Who is Ivo?" answered by all six minds at cycle 24, each answer scored for factual ' +
      'recall against the canonical record with its cited memories and contradiction status.',
    caption: '"Who is Ivo?" at cycle 24. At cycle 0 all six gave the same answer.',
    proves: 'The same question is put to every mind at every checkpoint and scored the same way.',
    article_section: '10 — What the six minds actually did',
    upload_order: 6,
    cropped: 'clipped to the notice and the six answers',
  });

  // 09 — mobile, for the article.
  const mobile = await browser.newPage({ viewport: MOBILE, deviceScaleFactor: 2 });
  await open(mobile, '/cycle/24', { viewport: MOBILE });
  await mobile.screenshot({ path: path.join(ARTICLE_DIR, 'article-09-mobile.png') });
  record(path.join(ARTICLE_DIR, 'article-09-mobile.png'), {
    ...common,
    route: '/cycle/24',
    cycle: 24,
    viewport: '390x844 @2x',
    width: 780,
    height: 1688,
    alt:
      'The exhibition on a phone at cycle 24, showing the title, the tagline "Six minds. ' +
      'One past. No room.", the run status bar and the first mind.',
    caption: 'The exhibition on a phone. Same data, same run, no separate mobile build.',
    proves: 'The live application is usable on a phone.',
    article_section: '16 — Try the project',
    upload_order: 9,
    cropped: 'none',
  });
  await mobile.close();
  await page.close();
}

async function rasters(browser) {
  console.log('\nCharts and diagrams');
  const chart = (name) => path.join(CHARTS, `${name}.svg`);

  await raster(
    browser,
    path.join(README_DIR, '08-architecture.svg'),
    path.join(README_DIR, '08-architecture.png'),
    1240,
    {
      source_url: 'docs/showcase/assets/readme/08-architecture.svg',
      route: null,
      run_id: null,
      cycle: null,
      canonical: false,
      fixture_mode: false,
      viewport: 'rasterised at 1240 CSS px, 2x',
      alt:
        'The deployed AWS architecture: EventBridge Scheduler invokes a Run-Cycle Lambda ' +
        'that calls Bedrock and commits six arms atomically to DynamoDB, emits a ' +
        'cycle-completed event to an Analysis Lambda, while CloudFront serves a private S3 ' +
        'frontend and an API Gateway read path backed by a read-only Lambda, with the ' +
        'dataset export in a second private bucket.',
      caption:
        'Everything the project runs in us-east-1. Both switches that let a cycle ' +
        'happen are currently off.',
      proves: 'The system is a real serverless deployment, not a local script.',
      readme_section: '11 — AWS Architecture',
    },
  );

  await raster(
    browser,
    path.join(README_DIR, 'cycle-sequence.svg'),
    path.join(README_DIR, 'cycle-sequence.png'),
    1240,
    {
      source_url: 'docs/showcase/assets/readme/cycle-sequence.svg',
      route: null,
      run_id: null,
      cycle: null,
      canonical: false,
      fixture_mode: false,
      viewport: 'rasterised at 1240 CSS px, 2x',
      alt:
        'A sequence diagram of one cycle across nine participants, from the EventBridge ' +
        'Scheduler through the Run-Cycle Lambda, Bedrock, the policy engine and DynamoDB to ' +
        'the analysis path and the public read API.',
      caption:
        'One cycle, thirteen steps. Steps one to eight are the experiment; everything ' +
        'after step eight only reads what step eight committed.',
      proves: 'The write path, the analysis path and the read path are separate by construction.',
      readme_section: '5 — How It Works',
    },
  );

  const chartMeta = {
    'origin-recall': {
      file: '07-results.png',
      dir: README_DIR,
      alt:
        'Origin Recall at cycles 0, 12 and 24 for all six minds. Every mind starts at 1.00. ' +
        'At cycle 24 Present-Minded holds 0.50, Pragmatist and Gambler 0.33, Keeper of the ' +
        'First Day 0.17, and Goldfish and Dreamer 0.00.',
      caption:
        'Share of six canonical facts each mind can still state, at the three ' +
        'checkpoints. One run, one seed world: this shows the mechanisms separated, not by ' +
        'how much they separate in general.',
      proves: 'The six mechanisms finished the run at measurably different levels of recall.',
      readme_section: '10 — Key Results',
    },
    'prediction-scorecard': {
      file: 'article-08-prediction-scorecard.png',
      dir: ARTICLE_DIR,
      alt:
        'The eight preregistered predictions graded against the canonical run: two ' +
        'supported, two partially supported, two not supported and two inconclusive.',
      caption: 'Predictions registered before the run, graded after it. Two failed outright.',
      proves: 'The predictions were written down first and the failures are published.',
      article_section: '11 — Predictions versus results',
      upload_order: 8,
    },
  };

  for (const [name, meta] of Object.entries(chartMeta)) {
    const { file, dir, ...rest } = meta;
    await raster(browser, chart(name), path.join(dir, file), 1200, {
      source_url: `docs/showcase/assets/charts/${name}.svg`,
      route: null,
      run_id: RUN_ID,
      cycle: null,
      canonical: true,
      fixture_mode: false,
      viewport: 'rasterised at 1200 CSS px, 2x',
      cropped: 'none',
      ...rest,
    });
  }

  // The article carries the same architecture, cycle flow and results chart.
  await raster(
    browser,
    path.join(README_DIR, 'cycle-sequence.svg'),
    path.join(ARTICLE_DIR, 'article-03-cycle-flow.png'),
    1200,
    {
      source_url: 'docs/showcase/assets/readme/cycle-sequence.svg',
      route: null,
      run_id: null,
      cycle: null,
      canonical: false,
      fixture_mode: false,
      viewport: 'rasterised at 1200 CSS px, 2x',
      alt: 'The thirteen steps of one cycle, from the scheduler to the public read API.',
      caption: 'One cycle. Six arms are written in a single transaction, or none of them are.',
      proves: 'The cycle is a bounded, atomic unit of work.',
      article_section: '4 — What happens during one cycle',
      upload_order: 3,
      cropped: 'none',
    },
  );

  await raster(
    browser,
    path.join(README_DIR, '08-architecture.svg'),
    path.join(ARTICLE_DIR, 'article-04-architecture.png'),
    1200,
    {
      source_url: 'docs/showcase/assets/readme/08-architecture.svg',
      route: null,
      run_id: null,
      cycle: null,
      canonical: false,
      fixture_mode: false,
      viewport: 'rasterised at 1200 CSS px, 2x',
      alt:
        'The deployed AWS architecture for Attention Sink in us-east-1, showing the ' +
        'scheduled generation path, persistence, asynchronous analysis, the public read ' +
        'path, the dataset export and the cross-cutting monitoring.',
      caption: 'Eleven AWS services. The account identifier is masked and no ARN is complete.',
      proves:
        'The deployment is serverless end to end and nothing in it is public except ' +
        'CloudFront.',
      article_section: '7 — AWS architecture',
      upload_order: 4,
      cropped: 'none',
    },
  );

  await raster(
    browser,
    chart('origin-recall'),
    path.join(ARTICLE_DIR, 'article-07-results-chart.png'),
    1200,
    {
      source_url: 'docs/showcase/assets/charts/origin-recall.svg',
      route: null,
      run_id: RUN_ID,
      cycle: null,
      canonical: true,
      fixture_mode: false,
      viewport: 'rasterised at 1200 CSS px, 2x',
      alt:
        'Origin Recall at cycles 0, 12 and 24 for all six minds, falling from 1.00 for ' +
        'every mind to between 0.50 and 0.00.',
      caption:
        'What survived. Present-Minded finished highest at 0.50; two minds finished ' + 'at zero.',
      proves:
        'Recall was measured identically for every mechanism and every mechanism lost ' + 'ground.',
      article_section: '10 — What the six minds actually did',
      upload_order: 7,
      cropped: 'none',
    },
  );

  // The deployment-evidence card, rendered from the JSON the collector wrote.
  const evidence = path.join(SOURCE, 'deployment-evidence.html');
  if (!existsSync(evidence)) {
    fail('no deployment-evidence.html; run scripts/build_deployment_evidence.py first');
  }
  const page = await browser.newPage({
    viewport: { width: 1280, height: 900 },
    deviceScaleFactor: 2,
  });
  await page.goto(pathToFileURL(evidence).href, { waitUntil: 'load' });
  await page.waitForTimeout(300);
  const main = await page.locator('main').boundingBox();
  const target = path.join(README_DIR, '09-aws-autonomy-proof.png');
  await page.screenshot({ path: target, clip: main });
  record(target, {
    source_url: 'docs/showcase/assets/source/deployment-evidence.html',
    route: null,
    run_id: RUN_ID,
    cycle: 24,
    canonical: true,
    fixture_mode: false,
    viewport: '1280 CSS px, 2x',
    width: Math.round(main.width * 2),
    height: Math.round(main.height * 2),
    alt:
      'A deployment-evidence card listing the canonical run, its twenty-four committed ' +
      'cycles, the EventBridge schedule name, expression and disabled state, ninety-five ' +
      'run-cycle Lambda invocations with zero errors, the execution switch set to false, the ' +
      'live exhibition and API health, and the published dataset — each row beside the ' +
      'command that produced it.',
    caption:
      'Rendered from the output of the commands in the right-hand column, with the ' +
      'account identifier masked. Not an AWS console screenshot and not a page of the ' +
      'exhibition.',
    proves:
      'The experiment advanced under an EventBridge schedule against real Lambda ' +
      'invocations, and both switches that allow a cycle are now off.',
    readme_section: '6 — Working Product Evidence',
  });
  await page.close();
}

// ------------------------------------------------------------------- walkthrough

const WALK = [
  { route: '/cycle/24', hold: 5, label: 'six minds at cycle 24' },
  { route: '/cycle/24', scroll: 900, hold: 4, label: 'the panels compared' },
  { route: '/graveyard?sort=oldest', hold: 5, label: 'the Graveyard' },
  { route: '/memory/mem_arm_fifo_000000', hold: 5, label: 'one forgotten memory' },
  { route: '/echoes?category=partial_reconstruction', hold: 5, label: 'its later echo' },
  { route: '/interviews?cycle=24&question=q01', hold: 5, label: 'who are you, at cycle 24' },
  { route: '/interviews?cycle=24&question=q01', scroll: 700, hold: 4, label: 'six answers' },
  { route: '/timeline', hold: 5, label: 'twenty-four cycles' },
];

async function walkthrough(browser) {
  console.log('\nWalkthrough');
  const frames = path.join(ROOT, '.showcase-frames');
  rmSync(frames, { recursive: true, force: true });
  mkdirSync(frames, { recursive: true });

  const page = await browser.newPage({
    viewport: { width: 1200, height: 760 },
    deviceScaleFactor: 1,
  });
  let index = 0;
  for (const step of WALK) {
    await open(page, step.route, { viewport: { width: 1200, height: 760 }, settle: 900 });
    if (step.scroll) {
      await page.evaluate((y) => window.scrollTo({ top: y, behavior: 'instant' }), step.scroll);
      await page.waitForTimeout(400);
    }
    for (let i = 0; i < step.hold; i += 1) {
      await page.screenshot({ path: path.join(frames, `f${String(index).padStart(3, '0')}.png`) });
      index += 1;
    }
    console.log(`  ${step.label} — ${step.hold} frames`);
  }
  await page.close();

  const gif = path.join(README_DIR, 'optional-demo.gif');
  const palette = path.join(frames, 'palette.png');
  const common = ['-y', '-framerate', '2', '-i', path.join(frames, 'f%03d.png')];
  const one = spawnSync(
    'ffmpeg',
    [...common, '-vf', 'scale=900:-1:flags=lanczos,palettegen=max_colors=96', palette],
    { stdio: 'ignore' },
  );
  const two =
    one.status === 0
      ? spawnSync(
          'ffmpeg',
          [
            ...common,
            '-i',
            palette,
            '-lavfi',
            'scale=900:-1:flags=lanczos [x]; [x][1:v] paletteuse=dither=bayer:bayer_scale=3',
            '-loop',
            '0',
            gif,
          ],
          { stdio: 'ignore' },
        )
      : { status: 1 };

  if (two.status === 0 && existsSync(gif) && statSync(gif).size > 0) {
    record(gif, {
      source_url: APP,
      route: WALK.map((s) => s.route).join(' → '),
      run_id: RUN_ID,
      cycle: 24,
      canonical: true,
      fixture_mode: false,
      viewport: '1200x760, scaled to 900 wide',
      width: 900,
      height: 570,
      alt:
        'A recorded walkthrough of the deployed exhibition: the six minds at cycle 24, the ' +
        'Graveyard, one forgotten memory, its later echo, the interview answers to "Who are ' +
        'you?" and the twenty-four-cycle timeline.',
      caption: 'Eighteen seconds through the live exhibition, at two frames per second.',
      proves: 'The whole product works end to end in a browser against the canonical run.',
      readme_section: '1 — Hero',
    });
    rmSync(frames, { recursive: true, force: true });
    return true;
  }

  console.log('  ffmpeg produced no usable GIF; falling back to a static montage');
  const montage = path.join(README_DIR, 'demo-walkthrough.png');
  const still = await browser.newPage({ viewport: { width: 1200, height: 760 } });
  await open(still, '/graveyard?sort=oldest', { viewport: { width: 1200, height: 760 } });
  await still.screenshot({ path: montage });
  record(montage, {
    source_url: APP,
    route: '/graveyard?sort=oldest',
    run_id: RUN_ID,
    cycle: null,
    canonical: true,
    fixture_mode: false,
    viewport: '1200x760',
    width: 1200,
    height: 760,
    alt: 'A still of the deployed Graveyard, used in place of an animated walkthrough.',
    caption: 'Fallback still: no usable animation could be produced on this machine.',
    proves: 'The walkthrough route exists even where a GIF could not be encoded.',
    readme_section: '1 — Hero',
  });
  await still.close();
  rmSync(frames, { recursive: true, force: true });
  return false;
}

// ---------------------------------------------------------------- written record

/**
 * The two asset manifests and the capture report, written from the same records the
 * screenshots were taken with. Generated rather than maintained by hand: a manifest
 * that can drift from the images it describes is a manifest nobody can trust.
 */
function writeRecord(run) {
  const readme = records.filter((r) => r.readme_section);
  const article = records
    .filter((r) => r.article_section)
    .sort((a, b) => (a.upload_order ?? 99) - (b.upload_order ?? 99));

  const provenance = (r) => (r.route ? `${APP}${r.route}` : r.source_url);

  const readmeBody = readme
    .map((r) =>
      [
        `### \`${r.filename}\``,
        '',
        `| Field | Value |`,
        `| --- | --- |`,
        `| Source | ${provenance(r)} |`,
        `| Route | ${r.route ?? 'rendered from a repository source file'} |`,
        `| Run | ${r.run_id ?? 'not run-specific'} |`,
        `| Cycle | ${r.cycle ?? 'not cycle-specific'} |`,
        `| Viewport | ${r.viewport} |`,
        `| Pixels | ${r.width} x ${r.height} |`,
        `| Size | ${(r.bytes / 1024).toFixed(0)} KB |`,
        `| Digest | \`${r.digest}\` |`,
        `| Captured | ${r.captured_at} |`,
        `| Canonical data | ${r.canonical ? 'yes' : 'no — diagram, not data'} |`,
        `| Fixture mode | no |`,
        `| README section | ${r.readme_section} |`,
        '',
        `**Alt text.** ${r.alt}`,
        '',
        `**Caption.** ${r.caption}`,
        '',
        `**What it proves.** ${r.proves}`,
        '',
      ].join('\n'),
    )
    .join('\n');

  writeFileSync(
    path.join(ROOT, 'docs/showcase/README_ASSET_MANIFEST.md'),
    `# README asset manifest

Every image the README carries, where it came from, and what it is evidence of.
Generated by \`scripts/capture_showcase_screenshots.mjs\`; do not edit by hand.

Captured from ${APP} against \`${run.run_id}\` — ${run.run_kind}, ${run.status}, ${run.current_cycle} of ${run.maximum_cycles} cycles, protocol ${run.protocol_version}, a ${run.memory_budget_tokens}-token budget measured by \`${run.token_count_source}\`. Nothing in this list came from a fixture run, a local build, a mock, or a hand-edited file.

${
  readme.some((r) => r.filename.endsWith('.gif'))
    ? 'The animated walkthrough is `optional-demo.gif`, encoded with ffmpeg from frames\ncaptured on the deployed site. No fallback montage was needed.'
    : 'No usable animation could be encoded on the capture machine, so the README uses\n`demo-walkthrough.png` as the documented static fallback.'
}

${readmeBody}`.replace(/\n{3,}/g, '\n\n'),
  );

  const articleBody = article
    .map((r) =>
      [
        `### ${r.upload_order}. \`${r.filename}\``,
        '',
        `| Field | Value |`,
        `| --- | --- |`,
        `| Article section | ${r.article_section} |`,
        `| Upload order | ${r.upload_order} |`,
        `| Source | ${provenance(r)} |`,
        `| Run | ${r.run_id ?? 'not run-specific'} |`,
        `| Cycle | ${r.cycle ?? 'not cycle-specific'} |`,
        `| Pixels | ${r.width} x ${r.height} |`,
        `| Size | ${(r.bytes / 1024).toFixed(0)} KB |`,
        `| Digest | \`${r.digest}\` |`,
        `| Cropping or redaction | ${r.cropped ?? 'none'} |`,
        `| Canonical evidence | ${r.canonical ? 'yes — the canonical run' : 'no — a diagram of the deployment'} |`,
        '',
        `**Alt text.** ${r.alt}`,
        '',
        `**Caption.** ${r.caption}`,
        '',
      ].join('\n'),
    )
    .join('\n');

  writeFileSync(
    path.join(ROOT, 'docs/showcase/ARTICLE_ASSET_MANIFEST.md'),
    `# Article asset manifest

The nine images the AWS Builder Center article uploads, in upload order, with the
caption and alt text each one needs. Generated by
\`scripts/capture_showcase_screenshots.mjs\`; do not edit by hand.

Builder Center uploads images one at a time, so the paste-ready article carries a
numbered marker where each of these belongs. The order below is that order.

${articleBody}`.replace(/\n{3,}/g, '\n\n'),
  );

  const rejected = [
    'A local build: it renders the LOCAL SIMULATION banner, which the capture refuses.',
    'A fixture run: the footer says simulated model outputs, which the capture refuses.',
    'Storybook or component fixtures: none exist in this repository.',
    'A staging deployment: it holds three cycles and no results.',
    'Hand-edited JSON: every figure is read live from the read API at capture time.',
  ];

  writeFileSync(
    path.join(ROOT, 'docs/showcase/SCREENSHOT_CAPTURE_REPORT.md'),
    `# Screenshot capture report

Written by \`scripts/capture_showcase_screenshots.mjs\` on its last successful run.

## What was captured, and from where

| | |
| --- | --- |
| Exhibition | ${APP} |
| Read API | ${API} |
| Run | \`${run.run_id}\` — ${run.run_kind}, ${run.status} |
| Cycles | ${run.current_cycle} of ${run.maximum_cycles} |
| Protocol | ${run.protocol_version}, ${run.memory_budget_tokens} tokens, \`${run.token_count_source}\` |
| Assets written | ${records.length} |
| Captured at | ${new Date().toISOString().replace(/\.\d+Z$/, 'Z')} |

## What the capture checked before it took anything

Four checks, all of which a non-production site fails:

1. The read API reports \`run_kind\` \`aws_canonical\`, not a local or staging kind.
2. The run reports status \`completed\` at cycle 24 of 24.
3. No page under capture renders the \`simulated-banner\` element.
4. Every page's own footer states that its words came from real model outputs.

Two further checks are made per shot rather than up front: the Graveyard's oldest
entry must be the seed name memory, and the interview view must return exactly six
answers. A change to either fails the capture rather than producing a wrong caption.

## What was refused

${rejected.map((r) => `- ${r}`).join('\n')}

## The assets

| File | Pixels | Size | Source |
| --- | --- | --- | --- |
${records.map((r) => `| \`${r.filename}\` | ${r.width} x ${r.height} | ${(r.bytes / 1024).toFixed(0)} KB | ${r.route ? `\`${r.route}\`` : r.source_url.replace('docs/showcase/assets/', '')} |`).join('\n')}

## Rerunning it

\`\`\`bash
make showcase            # all of the below, in order

make showcase-charts     # refresh the numbers and redraw the diagrams
make showcase-evidence   # re-collect the AWS evidence card
make showcase-capture    # re-take every image from the deployed site
make showcase-release    # rebuild the paste-ready article and the package
make showcase-verify     # check the result
\`\`\`

The capture needs no AWS credential — it reads the public site and the public API.
Only \`build_deployment_evidence.py\` uses the default credential chain, and it reads
three describe-style calls and writes nothing.
`,
  );

  const files = [
    'docs/showcase/README_ASSET_MANIFEST.md',
    'docs/showcase/ARTICLE_ASSET_MANIFEST.md',
    'docs/showcase/SCREENSHOT_CAPTURE_REPORT.md',
  ];
  const formatted = spawnSync('npx', ['prettier', '--write', ...files], {
    cwd: ROOT,
    stdio: 'ignore',
  });
  console.log(
    formatted.status === 0
      ? `wrote and formatted ${files.length} generated documents`
      : `wrote ${files.length} generated documents (prettier did not run)`,
  );
}

// --------------------------------------------------------------------------- run

const run = await preflight();
mkdirSync(README_DIR, { recursive: true });
mkdirSync(ARTICLE_DIR, { recursive: true });

const browser = await chromium.launch();
try {
  await readmeShots(browser, run);
  await articleShots(browser, run);
  await rasters(browser);
  if (!skipGif) await walkthrough(browser);
} finally {
  await browser.close();
}

records.sort((a, b) => a.filename.localeCompare(b.filename));
writeFileSync(
  path.join(SOURCE, 'screenshot-metadata.json'),
  `${JSON.stringify(
    {
      captured_from: APP,
      read_api: API,
      run_id: RUN_ID,
      run_kind: run.run_kind,
      run_status: run.status,
      cycles: `${run.current_cycle}/${run.maximum_cycles}`,
      protocol_version: run.protocol_version,
      memory_budget_tokens: run.memory_budget_tokens,
      token_count_source: run.token_count_source,
      fixture_mode: false,
      assets: records,
    },
    null,
    2,
  )}\n`,
);
console.log(
  `\nwrote docs/showcase/assets/source/screenshot-metadata.json — ${records.length} assets`,
);
writeRecord(run);
