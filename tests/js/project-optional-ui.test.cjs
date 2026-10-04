const test = require('node:test');
const assert = require('node:assert/strict');
const { projectMount, flush } = require('./project-harness.cjs');
const project = { articles: [{ name: 'paper.pdf', status: 'ok', effects: 1, raw_response: '{}' }], pdf_count: 1, total_tokens: 2, successful_count: 1, failed_count: 0 };

test('optional hints, credential copy, pagination, and response panels may be absent', async t => {
  const h = projectMount('', { project, setup(window) {
    for (const selector of ['[data-model-hint]', '[data-model-validation]', '[data-openrouter-warning]', '.project-settings-message', '#parallel-requests', '#request-delay-sec', '[data-provider-label]', '[data-run-credential-prompt]', '[data-run-actions]', '[data-key-heading]', '[data-key-prompt-copy]', '[data-key-input]', '[data-macos-key-note]', '[data-article-pagination]', '[data-articles-detail-view]']) window.document.querySelector(selector).remove();
  } });
  t.after(h.close); await flush();
  h.select('openrouter'); await flush();
  assert.equal(h.document.querySelector('.js-run').disabled, false);
  await h.click('.js-save-project-settings');
  assert.ok(h.calls.some(([url]) => url.endsWith('/settings')));
  await h.click('.js-view-response');
  assert.equal(h.document.querySelector('[data-articles-table-view]').hidden, false);
  assert.deepEqual(h.browserErrors, []);
});

test('project refresh works without an article table or model suggestion list', async t => {
  const h = projectMount('', { project, setup(window) {
    window.document.querySelector('[data-article-body]').remove();
    window.document.querySelector('#gemini-model-options').remove();
  } });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('[data-pdf-count]').textContent, '1 PDFs');
  assert.equal(h.document.querySelector('[data-token-count]').textContent, '2');
  h.select('openrouter'); await flush();
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

test('a fixed provider input supports model loading and credential state', async t => {
  const h = projectMount('', { project, setup(window) {
    window.document.querySelector('#provider').outerHTML = '<input id="provider" name="provider" value="gemini" data-key-ready="true">';
  }, fetch: async url => url.includes('/api/models/') ? { ok: true, json: async () => [] } : undefined });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('[data-provider-label]').textContent, 'Gemini');
  assert.equal(h.document.querySelector('.js-run').disabled, false);
  assert.equal(h.document.querySelector('#model').placeholder, 'Enter a model ID');
  assert.deepEqual(h.browserErrors, []);
});

test('missing provider controls leave the rest of the project page functional', async t => {
  const h = projectMount('', { project, setup(window) { window.document.querySelector('#provider').remove(); } });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('.article-name').textContent, 'paper.pdf');
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
