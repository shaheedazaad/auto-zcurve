const test = require('node:test');
const assert = require('node:assert/strict');
const { projectMount, flush } = require('./project-harness.cjs');
const project = { articles: [{ name: 'paper.pdf', status: 'ok', effects: 1, raw_response: '{}' }], pdf_count: 1, total_tokens: 2, successful_count: 1, failed_count: 0 };

test('optional run notices, links, pagination, and response panels may be absent', async t => {
  const h = projectMount('', { project, setup(window) {
    for (const selector of ['[data-run-credential-prompt]', '[data-run-actions]', '[data-edit-extractor-link]', '[data-extractor-deleted]', '[data-article-pagination]', '[data-articles-detail-view]']) window.document.querySelector(selector).remove();
  } });
  t.after(h.close); await flush();
  h.select('nokey'); await flush();
  assert.equal(h.document.querySelector('.js-run').disabled, true);
  assert.ok(h.calls.some(([url]) => url.endsWith('/settings')));
  await h.click('.js-view-response');
  assert.equal(h.document.querySelector('[data-articles-table-view]').hidden, false);
  assert.deepEqual(h.browserErrors, []);
});

test('project refresh works without an article table', async t => {
  const h = projectMount('', { project, setup(window) {
    window.document.querySelector('[data-article-body]').remove();
  } });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('[data-pdf-count]').textContent, '1 file');
  assert.equal(h.document.querySelector('[data-token-count]').textContent, '2');
  assert.deepEqual(h.browserErrors, []);
});

test('active analysis disables schema editing and works without a slow-run hint', async t => {
  const h = projectMount('<button class="js-save-schema">Save schema</button>', { project, setup(window) {
    window.document.querySelector('[data-slow-extraction-warning]').remove();
  } });
  t.after(h.close); await flush(); await h.click('.js-regenerate-report');
  assert.equal(h.document.querySelector('.js-save-schema').disabled, true);
  await h.sources[0].emit('progress', { completed: 0, total: 1, message: 'Waiting' });
  h.timers[0]();
  await h.sources[0].emit('status', { status: 'cancelled', message: 'Stopped' });
  assert.equal(h.document.querySelector('.js-save-schema').disabled, false);
  assert.deepEqual(h.browserErrors, []);
});

test('a project page without extractors stays functional but cannot run', async t => {
  const h = projectMount('', { project, setup(window) { window.document.querySelector('.js-run-settings').remove(); } });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('.article-name').textContent, 'paper.pdf');
  assert.equal(h.document.querySelector('.js-run').disabled, true);
  await h.click('.js-open-folder');
  assert.equal(h.document.querySelector('.js-open-folder').textContent, 'Folder opened');
  assert.deepEqual(h.browserErrors, []);
});

test('returning to articles tolerates a removed detail panel', async t => {
  const partial = projectMount('<button data-response-back>Back</button>', { project, setup(window) {
    window.document.querySelector('[data-articles-detail-view]').remove();
  } });
  t.after(partial.close); await flush(); await partial.click('[data-response-back]');
  assert.equal(partial.document.querySelector('[data-articles-table-view]').hidden, false);
  assert.deepEqual(partial.browserErrors, []);
});
