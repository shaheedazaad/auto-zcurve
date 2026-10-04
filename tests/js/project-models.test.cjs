const test = require('node:test');
const assert = require('node:assert/strict');
const { projectMount, flush } = require('./project-harness.cjs');
const catalog = [{ id: 'gemini-test', name: '<safe model>', parallel_requests: 5, request_delay_sec: 0.5 }, { id: 'other', name: 'Other' }];
const ready = window => { window.document.querySelector('[value=gemini]').dataset.keyReady = 'true'; };

test('Gemini catalog preserves model choice, escapes labels, and supplies concurrency defaults', async t => {
  const h = projectMount('', { setup: ready, fetch: async url => url.includes('/api/models/') ? { ok: true, json: async () => catalog } : undefined });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('#model').value, 'gemini-test');
  assert.equal(h.document.querySelector('#model').getAttribute('list'), 'gemini-model-options');
  assert.equal(h.document.querySelector('#gemini-model-options option').textContent, '<safe model>');
  assert.equal(h.document.querySelector('#gemini-model-options safe'), null);
  h.input('#model', 'gemini-test', 'change');
  assert.equal(h.document.querySelector('#parallel-requests').value, '5');
  assert.equal(h.document.querySelector('#request-delay-sec').value, '0.5');
  h.input('#model', 'other', 'change');
  assert.equal(h.document.querySelector('#parallel-requests').value, '5');
  h.input('#parallel-requests', '9'); h.input('#model', 'gemini-test', 'change');
  assert.equal(h.document.querySelector('#parallel-requests').value, '9');
  h.input('#model', 'new-custom-model'); assert.match(h.document.querySelector('[data-model-hint]').textContent, /original PDF/);
});

test('a missing catalog model is retained with a warning', async t => {
  const h = projectMount('', { setup: ready, fetch: async url => url.includes('/api/models/') ? { ok: true, json: async () => [] } : undefined });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('#model').value, 'gemini-test');
  assert.match(h.document.querySelector('.run-message').textContent, /will still be used as entered/);
  assert.ok(h.document.querySelector('.run-message').classList.contains('u-warning'));
});

test('catalog failures and malformed validation responses show useful errors', async t => {
  for (const malformed of [false, true]) {
    const h = projectMount('', { setup: ready, fetch: async url => url.includes('/api/models/') ? { ok: false, json: async () => { if (malformed) throw new Error('Invalid JSON'); return { detail: 'Provider unavailable' }; } } : undefined });
    t.after(h.close); await flush();
    assert.equal(h.document.querySelector('#model').placeholder, 'Model catalog unavailable');
    assert.equal(h.document.querySelector('.run-message').textContent, malformed ? 'Compatible models could not be loaded.' : 'Provider unavailable');
    assert.equal(h.document.querySelector('.js-run').disabled, true);
    h.select('openrouter'); await flush(); await h.click('.js-save-project-settings');
    assert.equal(h.document.querySelector('[data-model-validation]').textContent, malformed ? 'OpenRouter model validation failed.' : 'Provider unavailable');
    assert.equal(h.calls.some(([url]) => url.endsWith('/settings')), false);
  }
});

test('project credential save and unlock enable the provider and refresh unlocked models', async t => {
  const html = `<form class="js-credentials" action="/credentials"><input name="provider" value="gemini"><input name="api_key" value="secret"><p class="form-message"></p></form><form class="js-load-credentials" action="/unlock"><input name="provider" value="gemini"><p class="form-message"></p></form>`;
  const h = projectMount(html, { project: { articles: [], pdf_count: 1, total_tokens: 0, successful_count: 0, failed_count: 0 }, fetch: async url => url.includes('/api/models/') ? { ok: true, json: async () => catalog } : undefined });
  t.after(h.close); await flush();
  await h.submit('.js-credentials'); h.timers[0]();
  assert.equal(h.document.querySelector('[value=gemini]').dataset.keyReady, 'true');
  assert.equal(h.document.querySelector('.js-run').disabled, false);
  await h.submit('.js-load-credentials'); h.timers[1](); await flush();
  assert.equal(h.document.querySelector('[value=gemini]').dataset.savedKey, 'true');
  assert.equal(h.document.querySelector('#gemini-model-options').children.length, 2);
});

test('loading a catalog without an existing model leaves model selection empty', async t => {
  const h = projectMount('', { setup: window => { ready(window); window.document.querySelector('#model').value = ''; }, fetch: async url => url.includes('/api/models/') ? { ok: true, json: async () => catalog } : undefined });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('#model').value, '');
  assert.equal(h.document.querySelector('.run-message').textContent, '');
});

test('saving credentials updates a fixed provider input and ignores unrelated providers', async t => {
  for (const provider of ['gemini', 'unrelated']) {
    const h = projectMount(`<form class="js-credentials" action="/credentials"><input name="provider" value="${provider}"><input name="api_key" value="secret"><p class="form-message"></p></form>`, { setup(window) {
      window.document.querySelector('#provider').outerHTML = '<input id="provider" value="gemini" data-key-ready="false">';
    } });
    t.after(h.close); await flush();
    await h.submit('.js-credentials'); h.timers[0]();
    assert.equal(h.document.querySelector('#provider').dataset.keyReady, provider === 'gemini' ? 'true' : 'false');
    assert.deepEqual(h.browserErrors, []);
  }
});
