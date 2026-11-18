const test = require('node:test');
const assert = require('node:assert/strict');
const { projectMount, flush } = require('./project-harness.cjs');

const routingHtml = (saved = '', fallbacks = 'allow') => `<div data-openrouter-routing hidden>
<select id="openrouter-provider" name="openrouter_provider"><option value="">Automatic (OpenRouter chooses)</option>${saved ? `<option value="${saved}" selected>${saved}</option>` : ''}</select>
<p data-openrouter-provider-status></p>
<select id="openrouter-fallbacks" name="openrouter_fallbacks"><option value="allow" ${fallbacks === 'allow' ? 'selected' : ''}>Allow</option><option value="deny" ${fallbacks === 'deny' ? 'selected' : ''}>Deny</option></select>
<p data-openrouter-fallbacks-hint></p></div>`;
const endpoints = [{ tag: 'akashml/fp8', name: 'AkashML (fp8)' }, { tag: 'deepinfra/turbo', name: '<b>DeepInfra</b> (turbo)' }];
const endpointFetch = (reply) => async (url) => url.includes('/api/models/openrouter/endpoints') ? reply(url) : undefined;
const ok = payload => ({ ok: true, json: async () => payload });
const $ = (h, selector) => h.document.querySelector(selector);
const change = (h, selector, value) => { const node = $(h, selector); node.value = value; node.dispatchEvent(new h.window.Event('change')); };

test('OpenRouter routing lists providers for the model, keeps the saved choice, and explains fallbacks', async t => {
  const h = projectMount(routingHtml('saved/tag', 'deny'), { fetch: endpointFetch(() => ok(endpoints)) });
  t.after(h.close); await flush();
  assert.equal($(h, '[data-openrouter-routing]').hidden, true);
  h.select('openrouter'); await flush();
  assert.equal($(h, '[data-openrouter-routing]').hidden, false);
  const call = h.calls.find(([url]) => url.includes('/endpoints'));
  assert.equal(call[0], '/token/api/models/openrouter/endpoints?model=vendor%2Ftest');
  const options = [...h.document.querySelectorAll('#openrouter-provider option')].map(node => [node.value, node.textContent]);
  assert.deepEqual(options, [['', 'Automatic (OpenRouter chooses)'], ['akashml/fp8', 'AkashML (fp8)'], ['deepinfra/turbo', '<b>DeepInfra</b> (turbo)'], ['saved/tag', 'saved/tag (not listed for this model)']]);
  assert.equal($(h, '#openrouter-provider b'), null);
  assert.equal($(h, '#openrouter-provider').value, 'saved/tag');
  assert.equal($(h, '[data-openrouter-provider-status]').textContent, '2 providers serve this model with structured output.');
  assert.equal($(h, '[data-openrouter-fallbacks-hint]').textContent, 'Only saved/tag (not listed for this model) is used. Requests fail if it is unavailable.');
  change(h, '#openrouter-provider', 'akashml/fp8');
  change(h, '#openrouter-fallbacks', 'allow');
  assert.equal($(h, '[data-openrouter-fallbacks-hint]').textContent, 'Tries AkashML (fp8) first, then other providers if it is unavailable.');
  change(h, '#openrouter-provider', '');
  assert.equal($(h, '[data-openrouter-fallbacks-hint]').textContent, 'OpenRouter picks a provider and tries others if a request fails.');
  change(h, '#openrouter-fallbacks', 'deny');
  assert.equal($(h, '[data-openrouter-fallbacks-hint]').textContent, "Only OpenRouter's first-choice provider is tried.");
  const form = new h.window.FormData($(h, '.js-run-settings'));
  assert.equal(form.get('provider'), 'openrouter');
  h.select('gemini'); await flush();
  assert.equal($(h, '[data-openrouter-routing]').hidden, true);
});

test('changing the OpenRouter model reloads providers and reports empty, single, and failed listings', async t => {
  const replies = [ok([]), ok([endpoints[0]]), { ok: false, json: async () => ({ detail: 'OpenRouter is down' }) }, { ok: false, json: async () => { throw new Error('bad json'); } }];
  const h = projectMount(routingHtml(), { fetch: endpointFetch(() => replies.shift()) });
  t.after(h.close); await flush();
  h.select('openrouter'); await flush();
  const status = () => $(h, '[data-openrouter-provider-status]').textContent;
  assert.equal(status(), 'No provider currently serves this model with structured output.');
  h.input('#model', 'vendor/one', 'change'); await flush();
  assert.equal(status(), '1 provider serves this model with structured output.');
  h.input('#model', 'vendor/two', 'change'); await flush();
  assert.equal(status(), 'OpenRouter is down');
  h.input('#model', 'vendor/three', 'change'); await flush();
  assert.equal(status(), 'Providers could not be loaded.');
  h.input('#model', '', 'change'); await flush();
  assert.equal(status(), 'Choose a model to see which providers serve it.');
  const loads = h.calls.filter(([url]) => url.includes('/endpoints')).length;
  h.select('gemini'); await flush(); h.input('#model', 'gemini-test', 'change'); await flush();
  assert.equal(h.calls.filter(([url]) => url.includes('/endpoints')).length, loads);
});

test('a slow provider listing for a previous model is ignored', async t => {
  const pending = [];
  const h = projectMount(routingHtml(), { fetch: endpointFetch(url => new Promise(resolve => pending.push({ url, resolve }))) });
  t.after(h.close); await flush();
  h.select('openrouter'); await flush();
  h.input('#model', 'vendor/second', 'change'); await flush();
  assert.equal(pending.length, 2);
  pending[1].resolve(ok([endpoints[1]])); await flush();
  pending[0].resolve(ok(endpoints)); await flush();
  assert.equal(h.document.querySelectorAll('#openrouter-provider option').length, 2);
  h.input('#model', 'vendor/third', 'change'); await flush();
  h.input('#model', 'vendor/fourth', 'change'); await flush();
  pending[3].resolve(ok([])); await flush();
  pending[2].resolve({ ok: false, json: async () => ({ detail: 'stale failure' }) }); await flush();
  assert.equal($(h, '[data-openrouter-provider-status]').textContent, 'No provider currently serves this model with structured output.');
});
