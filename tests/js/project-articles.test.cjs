const test = require('node:test');
const assert = require('node:assert/strict');
const { projectMount, flush } = require('./project-harness.cjs');
const articles = Array.from({ length: 12 }, (_, index) => ({ name: `Paper ${String(index).padStart(2, '0')}.pdf`, status: index === 0 ? 'error' : index === 1 ? 'ready' : 'ok', effects: index, input_tokens: index * 10, output_tokens: index, raw_response: index === 1 ? '' : JSON.stringify({ effects: [index], valid: true, missing: null }), error: index === 0 ? '<img src=x onerror=bad>' : '', warning: index === 2 ? 'Check result' : '', json_repaired: index === 2, repaired_response: index === 2 ? '{"fixed": true}' : '' }));
const project = { articles, pdf_count: 12, total_tokens: 660, successful_count: 10, failed_count: 1 };

test('articles paginate and sort numeric effects and names; provider output is safely rendered', async t => {
  const h = projectMount('', { project }); t.after(h.close); await flush();
  const rows = () => [...h.document.querySelectorAll('.article-name')].map(node => node.textContent);
  assert.equal(rows().length, 10); assert.equal(rows()[0], 'Paper 00.pdf');
  assert.equal(h.document.querySelector('[data-article-body] img'), null);
  assert.equal(h.document.querySelector('.article-message').textContent, '<img src=x onerror=bad>');
  assert.equal(h.document.querySelector('[data-pdf-count]').textContent, '12 files');
  assert.equal(h.document.querySelector('[data-token-count]').textContent, '660');
  await h.click('.js-page-next'); assert.equal(rows().length, 2);
  assert.equal(h.document.querySelector('[data-page-status]').textContent, 'Page 2 of 2');
  await h.click('.js-page-previous'); assert.equal(rows()[0], 'Paper 00.pdf');
  await h.click('[data-sort-key=effects]'); assert.equal(rows()[0], 'Paper 00.pdf');
  await h.click('[data-sort-key=effects]'); assert.equal(rows()[0], 'Paper 11.pdf');
  await h.click('[data-sort-key=name]'); assert.equal(rows()[0], 'Paper 00.pdf');
  await h.click('[data-sort-key=name]'); assert.equal(rows()[0], 'Paper 11.pdf');
  await h.click('.js-view-response[data-source-name="Paper 02.pdf"]');
  assert.equal(h.document.querySelector('[data-articles-table-view]').hidden, true);
  assert.equal(h.document.querySelector('[data-response-title]').textContent, 'Paper 02.pdf · Provider output');
  const panels = h.document.querySelectorAll('[data-response-body] pre');
  assert.equal(panels.length, 2); assert.deepEqual(JSON.parse(panels[0].textContent), { effects: [2], valid: true, missing: null });
  assert.deepEqual(JSON.parse(panels[1].textContent), { fixed: true });
  await h.click('[data-response-back]'); assert.equal(h.document.querySelector('[data-articles-detail-view]').hidden, true);
});

test('deleting a response requires confirmation, refreshes results, and recovers from rejection', async t => {
  for (const [confirm, ok] of [[false, true], [true, true], [true, false]]) {
    const h = projectMount('', { project, confirm, fetch: async (url, request) => request?.method === 'POST' ? { ok, json: async () => ({ detail: 'Cannot delete' }) } : undefined });
    t.after(h.close); await flush();
    await h.click('.js-delete-response');
    const posts = h.calls.filter(([, request]) => request?.method === 'POST');
    assert.equal(posts.length, Number(confirm));
    if (confirm) {
      assert.equal(posts[0][0], '/token/projects/project/responses/delete');
      assert.equal(posts[0][1].body.get('source_name'), 'Paper 00.pdf');
      if (ok) assert.equal(h.calls.at(-1)[0], '/token/api/projects/project');
      else { assert.deepEqual(h.alerts, ['Cannot delete']); assert.equal(h.document.querySelector('.js-delete-response').disabled, false); }
    }
  }
});

test('PDF uploads handle drops, selection, empty inputs, plural messages, and failures', async t => {
  const html = '<form class="js-upload" action="/upload"><input type="file" multiple></form><p class="upload-message layout"></p>';
  for (const [count, ok] of [[0, true], [1, true], [2, true], [1, false]]) {
    const h = projectMount(html, { fetch: async (url, request) => request?.method === 'POST' ? { ok, json: async () => ({ saved: Array(count).fill('paper.pdf'), detail: 'Invalid PDF' }) } : undefined });
    t.after(h.close); await flush();
    const upload = h.document.querySelector('.js-upload');
    for (const name of ['dragenter', 'dragover']) { upload.dispatchEvent(new h.window.Event(name, { cancelable: true })); assert.ok(upload.classList.contains('dragging')); }
    upload.dispatchEvent(new h.window.Event('dragleave')); assert.equal(upload.classList.contains('dragging'), false);
    const files = Array.from({ length: count }, () => new h.window.File(['pdf'], 'paper.pdf', { type: 'application/pdf' }));
    if (count === 2) {
      const input = upload.querySelector('input'); Object.defineProperty(input, 'files', { value: files }); input.dispatchEvent(new h.window.Event('change'));
    } else {
      const drop = new h.window.Event('drop', { cancelable: true }); Object.defineProperty(drop, 'dataTransfer', { value: { files } }); upload.dispatchEvent(drop);
    }
    await flush();
    const posts = h.calls.filter(([, request]) => request?.method === 'POST');
    assert.equal(posts.length, count ? 1 : 0);
    if (count) {
      assert.equal(posts[0][1].body.getAll('files').length, count);
      assert.equal(h.document.querySelector('.upload-message').textContent, ok ? `Added ${count} file${count === 1 ? '' : 's'}.` : 'Invalid PDF');
      assert.ok(h.document.querySelector('.upload-message').classList.contains('layout'));
    }
  }
});

test('JSON output highlights keys and strings and article names cannot inject attributes', async t => {
  const name = 'Paper " autofocus onfocus="alert(1).pdf';
  const h = projectMount('', { project: { ...project, articles: [{ name, status: 'ok', effects: 1, raw_response: '{"title":"result", "valid":true}' }] } });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('.article-name').getAttribute('title'), name);
  assert.equal(h.document.querySelector('[onfocus]'), null);
  assert.equal(h.document.querySelector('.js-view-response').dataset.sourceName, name);
  await h.click('.js-view-response');
  assert.equal(h.document.querySelector('[data-response-body] .json-key').textContent, '"title"');
  assert.equal(h.document.querySelector('[data-response-body] .json-string').textContent, '"result"');
});

test('non-JSON provider output is displayed literally', async t => {
  const raw = '<script>alert("unsafe")</script> not JSON';
  const h = projectMount('', { project: { ...project, articles: [{ name: 'Raw.pdf', status: 'error', raw_response: raw }] } });
  t.after(h.close); await flush(); await h.click('.js-view-response');
  assert.equal(h.document.querySelector('[data-response-body] pre').textContent, raw);
  assert.equal(h.document.querySelector('[data-response-body] script'), null);
});

test('unknown article states and missing sort values render safely, while failed refresh preserves the table', async t => {
  let failRefresh = false;
  const h = projectMount('', { project: { ...project, pdf_count: 2, successful_count: 1, articles: [
    { name: 'Unknown.pdf', status: 'future-status', raw_response: 'plain output' },
    { name: 'Done.pdf', status: 'ok', effects: 2, raw_response: '{}' },
  ] }, fetch: async url => failRefresh && url.includes('/api/projects/') ? { ok: false, json: async () => ({ detail: 'Unavailable' }) } : undefined });
  t.after(h.close); await flush();
  assert.match(h.document.querySelector('.js-run').textContent, /Continue processing 1 article/);
  await h.click('[data-sort-key=effects]');
  assert.equal(h.document.querySelector('.article-name').textContent, 'Unknown.pdf');
  assert.equal(h.document.querySelector('.article-status').textContent, 'Ready');
  const before = h.document.querySelector('[data-article-body]').innerHTML;
  failRefresh = true;
  await h.click('.js-regenerate-report');
  await h.intervals[0](); await flush();
  assert.equal(h.document.querySelector('[data-article-body]').innerHTML, before);
});

test('numeric sorting treats absent effect counts as zero on either side', async t => {
  const h = projectMount('', { project: { ...project, articles: [
    { name: 'Missing.pdf', status: 'ready' },
    { name: 'Two.pdf', status: 'ok', effects: 2 },
    { name: 'Also missing.pdf', status: 'ready' },
  ] } });
  t.after(h.close); await flush(); await h.click('[data-sort-key=effects]');
  const names = () => [...h.document.querySelectorAll('.article-name')].map(node => node.textContent);
  assert.equal(names().at(-1), 'Two.pdf');
  await h.click('[data-sort-key=effects]'); assert.equal(names()[0], 'Two.pdf');
});

test('needs-review extractions show a warning badge, their reasons, and enable retry without counting as remaining', async t => {
  const h = projectMount('', { project: { ...project, pdf_count: 3, successful_count: 1, needs_review_count: 2, failed_count: 0, articles: [
    { name: 'Repaired.pdf', status: 'needs_review', effects: 1, review_reasons: ['JSON was repaired', 'finish reason: <MAX_TOKENS>'] },
    { name: 'Legacy.pdf', status: 'needs_review', effects: 1, retry_error: 'network <timeout>' },
    { name: 'Done.pdf', status: 'ok', effects: 2 },
  ] } });
  t.after(h.close); await flush();
  const rows = [...h.document.querySelectorAll('.article-row')].reverse(); // sorted by name: Repaired, Legacy, Done
  assert.ok(rows[0].classList.contains('article-row-needs-review'));
  assert.ok(rows[2].classList.contains('article-row-ok'));
  const badge = rows[0].querySelector('.article-status');
  assert.equal(badge.textContent, 'Needs review'); assert.equal(badge.dataset.variant, 'warning');
  assert.equal(rows[0].querySelector('.article-message').textContent, 'Needs review: JSON was repaired; finish reason: <MAX_TOKENS>');
  assert.equal(rows[1].querySelector('.article-message').textContent, 'Needs review: no reason was recorded · Last retry failed: network <timeout>');
  assert.equal(h.document.querySelector('.js-retry').dataset.hasFailures, 'true');
  assert.doesNotMatch(h.document.querySelector('.js-run').textContent, /Continue processing/);
});

test('needs-review rows also show parser warnings, and every message is escaped', async t => {
  const h = projectMount('', { project: { ...project, pdf_count: 3, successful_count: 1, needs_review_count: 2, failed_count: 0, articles: [
    { name: 'A both.pdf', status: 'needs_review', review_reasons: ['finish reason: <MAX_TOKENS>'], warning: 'Parser <b>dropped</b> page 3' },
    { name: 'B reasons.pdf', status: 'needs_review', review_reasons: ['JSON was repaired'] },
    { name: 'C warning.pdf', status: 'ok', warning: '<img src=x onerror=bad> table skipped' },
  ] } });
  t.after(h.close); await flush();
  const messages = name => [...[...h.document.querySelectorAll('.article-row')].find(row => row.querySelector('.article-name').textContent === name).querySelectorAll('.article-message')].map(node => node.textContent);
  assert.deepEqual(messages('A both.pdf'), ['Needs review: finish reason: <MAX_TOKENS>', 'Parser <b>dropped</b> page 3']);
  assert.deepEqual(messages('B reasons.pdf'), ['Needs review: JSON was repaired']);
  assert.deepEqual(messages('C warning.pdf'), ['<img src=x onerror=bad> table skipped']);
  assert.equal(h.document.querySelector('[data-article-body] b, [data-article-body] img'), null);
});

test('header article, processed, and needs-review counts follow live project data', async t => {
  const header = '<span data-article-count>stale</span><span data-processed-count>stale</span>';
  const snapshot = (pdfs, successful, review, failed) => ({ ...project, articles: [], pdf_count: pdfs, successful_count: successful, needs_review_count: review, failed_count: failed });
  let live = snapshot(1, 0, 0, 0);
  const h = projectMount(header, { project: live, fetch: async url => url.includes('/api/projects/') ? { ok: true, json: async () => live } : undefined });
  t.after(h.close); await flush();
  const text = selector => h.document.querySelector(selector).textContent;
  assert.equal(text('[data-article-count]'), '1 article');
  assert.equal(text('[data-processed-count]'), '0 processed');
  await h.click('.js-run');
  await h.sources[0].emit('progress', { completed: 2, total: 3, message: 'Two done' });
  live = snapshot(3, 1, 1, 0); await h.intervals[0](); await flush();
  assert.equal(text('[data-article-count]'), '3 articles');
  assert.equal(text('[data-processed-count]'), '2 processed (1 need review)');
  live = snapshot(3, 1, 1, 1); await h.sources[0].emit('status', { status: 'failed', message: 'Failed' });
  assert.equal(text('[data-processed-count]'), '2 processed (1 need review)');
  await h.click('.js-retry');
  assert.equal(h.sources.length, 2);
  live = snapshot(3, 2, 0, 1); await h.sources[1].emit('status', { status: 'cancelled', message: 'Cancelled' });
  assert.equal(text('[data-processed-count]'), '2 processed');
});

const selectionHtml = `<select data-article-filter><option value="">All statuses</option><option value="ready">Ready</option><option value="ok">Done</option><option value="needs_review">Needs review</option><option value="error">Failed</option></select>
<input class="js-select-all-articles" type="checkbox">
<div data-article-selection hidden><span data-selection-count></span><button class="js-retry-selected">Retry selected</button><button class="js-delete-selected">Delete output</button><button class="js-clear-selection">Clear</button></div>
<button data-tab-target="run-analysis">Run analysis</button>`;
const tick = async (h, name, checked = true) => {
  const box = h.document.querySelector(`.js-select-article[value="${name}"]`);
  box.checked = checked; box.dispatchEvent(new h.window.Event('change', { bubbles: true })); await flush();
};

test('the status filter narrows the table, counts each status, and paginates the matches', async t => {
  const h = projectMount(selectionHtml, { project }); t.after(h.close); await flush();
  const rows = () => [...h.document.querySelectorAll('.article-name')].map(node => node.textContent);
  const options = [...h.document.querySelectorAll('[data-article-filter] option')].map(option => option.textContent);
  assert.deepEqual(options, ['All statuses (12)', 'Ready (1)', 'Done (10)', 'Needs review (0)', 'Failed (1)']);
  h.input('[data-article-filter]', 'error', 'change'); await flush();
  assert.deepEqual(rows(), ['Paper 00.pdf']);
  assert.equal(h.document.querySelector('[data-article-pagination]').hidden, true);
  h.input('[data-article-filter]', 'needs_review', 'change'); await flush();
  assert.equal(h.document.querySelector('.table-empty-cell').textContent, 'No articles have this status.');
  assert.equal(h.document.querySelector('.js-select-all-articles').disabled, true);
  h.input('[data-article-filter]', 'ok', 'change'); await flush();
  assert.equal(rows().length, 10);
  assert.equal(h.document.querySelector('[data-article-pagination]').hidden, true);
  assert.equal(h.document.querySelector('.js-page-next').disabled, true);
  h.input('[data-article-filter]', '', 'change'); await flush();
  await h.click('.js-page-next'); assert.equal(rows().length, 2);
});

test('selections survive paging, select-all follows the filter, and clearing hides the bar', async t => {
  const h = projectMount(selectionHtml, { project }); t.after(h.close); await flush();
  const bar = h.document.querySelector('[data-article-selection]');
  const selectAll = h.document.querySelector('.js-select-all-articles');
  assert.equal(bar.hidden, true);
  await tick(h, 'Paper 01.pdf');
  assert.equal(bar.hidden, false);
  assert.equal(h.document.querySelector('[data-selection-count]').textContent, '1 selected');
  assert.equal(selectAll.indeterminate, true);
  assert.equal(h.document.querySelector('.js-delete-selected').disabled, true); // only a Ready article is selected
  assert.equal(h.document.querySelector('.js-retry-selected').disabled, false);
  await h.click('.js-page-next'); await h.click('.js-page-previous');
  assert.equal(h.document.querySelector('.js-select-article[value="Paper 01.pdf"]').checked, true);
  await tick(h, 'Paper 01.pdf', false); assert.equal(bar.hidden, true);
  h.input('[data-article-filter]', 'ok', 'change'); await flush();
  selectAll.checked = true; selectAll.dispatchEvent(new h.window.Event('change')); await flush();
  assert.equal(h.document.querySelector('[data-selection-count]').textContent, '10 selected');
  assert.equal(selectAll.checked, true);
  assert.equal(h.document.querySelector('.js-delete-selected').disabled, false);
  h.document.querySelector('[data-article-body]').dispatchEvent(new h.window.Event('change', { bubbles: true }));
  selectAll.checked = false; selectAll.dispatchEvent(new h.window.Event('change')); await flush();
  assert.equal(bar.hidden, true);
  await tick(h, 'Paper 02.pdf'); await h.click('.js-clear-selection');
  assert.equal(bar.hidden, true);
  h.select('nokey'); await flush();
  await tick(h, 'Paper 02.pdf');
  assert.equal(h.document.querySelector('.js-retry-selected').disabled, true);
});

test('deleting selected output posts every chosen article with output, then refreshes', async t => {
  for (const [confirm, ok, picks] of [[false, true, ['Paper 00.pdf']], [true, true, ['Paper 00.pdf', 'Paper 01.pdf', 'Paper 02.pdf']], [true, true, ['Paper 03.pdf']], [true, false, ['Paper 00.pdf']]]) {
    const h = projectMount(selectionHtml, { project, confirm, fetch: async (url, request) => request?.method === 'POST' ? { ok, json: async () => ({ detail: 'Cannot delete' }) } : undefined });
    t.after(h.close); await flush();
    for (const name of picks) await tick(h, name);
    await h.click('.js-delete-selected');
    const posts = h.calls.filter(([, request]) => request?.method === 'POST');
    assert.equal(posts.length, Number(confirm));
    if (!confirm) continue;
    assert.equal(posts[0][0], '/token/projects/project/responses/delete');
    assert.deepEqual(posts[0][1].body.getAll('source_name'), picks.filter(name => name !== 'Paper 01.pdf'));
    if (ok) { assert.equal(h.calls.at(-1)[0], '/token/api/projects/project'); assert.equal(h.document.querySelector('[data-article-selection]').hidden, true); }
    else { assert.deepEqual(h.alerts, ['Cannot delete']); assert.equal(h.document.querySelector('.js-delete-selected').disabled, false); }
  }
});

test('retrying selected articles sends them with the extractor and opens the run tab', async t => {
  for (const [confirm, ok, picks] of [[false, true, ['Paper 01.pdf']], [true, true, ['Paper 01.pdf', 'Paper 03.pdf']], [true, true, ['Paper 04.pdf']], [true, false, ['Paper 04.pdf']]]) {
    let tabOpened = false;
    const h = projectMount(selectionHtml, { project, confirm, fetch: async (url, request) => request?.method === 'POST' ? { ok, json: async () => ({ detail: 'Already running' }) } : undefined });
    t.after(h.close); await flush();
    h.document.querySelector('[data-tab-target="run-analysis"]').addEventListener('click', () => { tabOpened = true; });
    for (const name of picks) await tick(h, name);
    await h.click('.js-retry-selected');
    const posts = h.calls.filter(([, request]) => request?.method === 'POST');
    assert.equal(posts.length, Number(confirm));
    if (!confirm) continue;
    assert.equal(posts[0][0], '/token/projects/project/retry');
    assert.deepEqual(posts[0][1].body.getAll('source_name'), picks);
    assert.equal(posts[0][1].body.get('extractor_id'), 'lab');
    assert.equal(tabOpened, ok);
    if (ok) {
      assert.equal(h.sources.length, 1);
      assert.equal(h.document.querySelector('[data-article-selection]').hidden, true);
    } else assert.deepEqual(h.alerts, ['Already running']);
  }
});

test('selections of articles that disappear are dropped, and done rows show a failed retry', async t => {
  let live = project;
  const h = projectMount(selectionHtml, { project, fetch: async url => url.includes('/api/projects/') ? { ok: true, json: async () => live } : undefined });
  t.after(h.close); await flush();
  await tick(h, 'Paper 03.pdf');
  live = { ...project, articles: [{ name: 'Paper 04.pdf', status: 'ok', retry_error: 'quota <exceeded>', warning: 'Parser note' }] };
  await h.click('.js-regenerate-report'); await h.intervals[0](); await flush();
  assert.equal(h.document.querySelector('[data-article-selection]').hidden, true);
  assert.deepEqual([...h.document.querySelectorAll('.article-message')].map(node => node.textContent), ['Last retry failed: quota <exceeded>', 'Parser note']);
});
