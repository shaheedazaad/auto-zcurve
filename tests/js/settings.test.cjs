const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mount } = require('./harness.cjs');

const settingsForm = '<form class="js-app-settings" action="/token/settings"><input name="parallel_requests" value="4" required><button type="submit">Save</button><p class="form-message settings-message mb-0 u-error">Old</p></form>';
const keyForm = '<form class="js-credentials" action="/token/credentials"><input name="api_key" value="secret"><input name="provider" value="gemini"><p class="form-message layout-class u-error"></p></form>';

test('settings submission sends values, preserves layout classes, and reenables submit', async () => {
  for (const options of [{ response: { saved: true } }, { ok: false, response: { detail: 'Rejected' } }, { reject: 'Offline' }, { ok: false, invalidJson: true }]) {
    const page = mount(settingsForm, options);
    try {
      await page.submit('.js-app-settings');
      assert.equal(page.calls.length, 1);
      assert.equal(page.calls[0][0], 'http://localhost/token/settings');
      assert.equal(page.calls[0][1].method, 'POST');
      assert.equal(page.calls[0][1].body.get('parallel_requests'), '4');
      const message = page.document.querySelector('.form-message');
      const expected = options.reject || options.response?.detail || (options.ok === false ? 'The request could not be completed.' : 'Settings saved.');
      assert.equal(message.textContent, expected);
      assert.equal(message.classList.contains(options.ok === false || options.reject ? 'u-error' : 'u-success'), true);
      assert.equal(message.classList.contains('settings-message'), true);
      assert.equal(message.classList.contains('mb-0'), true);
      assert.equal(page.document.querySelector('button').disabled, false);
    } finally { page.close(); }
  }
});

test('invalid settings do not submit', async () => {
  const page = mount(settingsForm);
  try {
    page.document.querySelector('input').value = '';
    await page.submit('form');
    assert.equal(page.calls.length, 0);
  } finally { page.close(); }
});

const extractor = { id: 'abc', name: 'Router', provider: 'openrouter', has_key: true, requires_key: true,
  settings: { name: 'Router', provider: 'openrouter', model: 'vendor/model', reasoning_effort: 'low', openrouter_provider: 'deepinfra/fp8', openrouter_allow_fallbacks: false, parallel_requests: 2, request_delay_sec: 0, request_timeout_sec: 600 } };
const local = { ...extractor, id: 'loc', name: 'Local', provider: 'openai_compatible', has_key: false, requires_key: false,
  settings: { name: 'Local', provider: 'openai_compatible', model: 'q', base_url: 'http://h/v1', response_format: 'none', context_length: null } };
const row = item => `<div class="row" id="extractor-${item.id}" data-extractor-row data-extractor='${JSON.stringify(item)}'><button data-edit-extractor>Edit</button>
<form class="js-extractor-action" action="/token/extractors/${item.id}/duplicate"></form>
<form class="js-delete-extractor" action="/token/extractors/${item.id}/delete" data-extractor-name="${item.name}"></form></div>`;
const extractorPage = `<nav class="settings-nav"><a href="#extractors">Extractors</a><a href="#files-and-parsing">Files</a></nav>
<button data-new-extractor>Add</button>${row(extractor)}${row(local)}<p class="form-message keep" data-extractor-list-message></p>
<dialog id="extractor-dialog"><form class="js-extractor-form" action="/token/extractors" data-create-action="/token/extractors">
<h2 data-extractor-dialog-title></h2>
<input name="name" required><select name="provider"><option value="gemini">Gemini</option><option value="openrouter">OpenRouter</option><option value="openai_compatible">Endpoint</option></select>
<p data-provider-locked hidden></p>
<input name="model" required><datalist id="extractor-model-options"></datalist><p data-model-hint></p><button type="button" data-load-models hidden>Load</button>
<div data-provider-fields="openrouter"><select name="reasoning_effort"><option value="high">High</option><option value="low">Low</option></select>
<input name="openrouter_provider"><datalist id="extractor-openrouter-endpoints"></datalist><p data-openrouter-provider-status></p>
<select name="openrouter_allow_fallbacks"><option value="allow">Allow</option><option value="deny">Deny</option></select></div>
<div data-provider-fields="gemini"><select name="service_tier"><option value="flex">Flex</option></select></div>
<div data-provider-fields="openai_compatible"><input name="base_url" required><select name="response_format"><option value="json_schema">Schema</option><option value="none">None</option></select><input name="context_length"></div>
<input name="parallel_requests" value="1"><input name="request_delay_sec" value="0"><input name="request_timeout_sec" value="600">
<label data-key-label></label><input name="api_key"><div data-key-actions hidden><button type="button" data-remove-key>Remove</button></div>
<p class="form-message"></p><button type="button" data-close-extractor-dialog>Cancel</button><button type="submit">Save</button></form></dialog>`;
const json = (body, ok = true) => ({ ok, json: async () => body });
const flush = () => new Promise(resolve => setImmediate(resolve));
const field = (page, name) => page.document.querySelector('.js-extractor-form').elements.namedItem(name);
const dialogMessage = page => page.document.querySelector('#extractor-dialog .form-message');

test('settings navigation marks the current section', () => {
  const page = mount(extractorPage);
  try {
    const links = page.document.querySelectorAll('.settings-nav a');
    assert.equal(links[0].getAttribute('aria-current'), 'location');
    page.window.location.hash = '#files-and-parsing';
    page.window.dispatchEvent(new page.window.Event('hashchange'));
    assert.equal(links[1].getAttribute('aria-current'), 'location');
    assert.equal(links[0].hasAttribute('aria-current'), false);
  } finally { page.close(); }
});

test('adding an extractor shows only its provider fields and saves it with its key', async () => {
  for (const [response, ok, expected, state] of [
    [{ warning: null }, true, 'Extractor saved.', 'u-success'],
    [{ warning: 'Saved without a key.' }, true, 'Saved without a key.', 'u-warning'],
    [{ detail: 'Bad model' }, false, 'Bad model', 'u-error'],
  ]) {
    const page = mount(extractorPage, { ok, response });
    try {
      page.document.querySelector('[data-new-extractor]').click();
      const dialog = page.document.querySelector('#extractor-dialog');
      assert.equal(dialog.open, true);
      assert.equal(page.document.querySelector('[data-extractor-dialog-title]').textContent, 'Add extractor');
      assert.equal(field(page, 'provider').disabled, false);
      assert.equal(page.document.querySelector('[data-provider-fields=gemini]').hidden, false);
      assert.equal(field(page, 'base_url').disabled, true);
      field(page, 'provider').value = 'openai_compatible';
      field(page, 'provider').dispatchEvent(new page.window.Event('change'));
      assert.equal(field(page, 'base_url').disabled, false);
      assert.match(page.document.querySelector('[data-model-hint]').textContent, /your server/);
      field(page, 'provider').value = 'openrouter';
      field(page, 'provider').dispatchEvent(new page.window.Event('change'));
      assert.equal(field(page, 'model').placeholder, 'vendor/model');
      field(page, 'name').value = 'New'; field(page, 'model').value = 'v/m'; field(page, 'api_key').value = 'secret';
      await page.submit('.js-extractor-form');
      assert.equal(page.calls.length, 1);
      assert.equal(page.calls[0][0], 'http://localhost/token/extractors');
      assert.equal(page.calls[0][1].body.get('api_key'), 'secret');
      assert.equal(page.calls[0][1].body.get('base_url'), null);
      assert.equal(dialogMessage(page).textContent, expected);
      assert.ok(dialogMessage(page).classList.contains(state));
      assert.equal(page.timers.length, ok ? 1 : 0);
      assert.equal(page.document.querySelector('button[type=submit]').disabled, false);
    } finally { page.close(); }
  }
});

test('an incomplete extractor form is not sent', async () => {
  const page = mount(extractorPage);
  try {
    page.document.querySelector('[data-new-extractor]').click();
    await page.submit('.js-extractor-form');
    assert.equal(page.calls.length, 0);
    page.document.querySelector('[data-close-extractor-dialog]').click();
    assert.equal(page.document.querySelector('#extractor-dialog').open, false);
  } finally { page.close(); }
});

test('editing fills the saved settings, keeps the provider, and replaces the key separately', async () => {
  const responses = [json({ warning: 'Model checked later.' }), json({ warning: 'Kept for this session.' })];
  const page = mount(extractorPage, { fetch: async () => responses.shift() });
  try {
    page.document.querySelector('#extractor-abc [data-edit-extractor]').click();
    assert.equal(page.document.querySelector('[data-extractor-dialog-title]').textContent, 'Edit Router');
    assert.equal(field(page, 'provider').disabled, true);
    assert.equal(field(page, 'provider').value, 'openrouter');
    assert.equal(field(page, 'openrouter_allow_fallbacks').value, 'deny');
    assert.equal(field(page, 'openrouter_provider').value, 'deepinfra/fp8');
    assert.equal(field(page, 'reasoning_effort').value, 'low');
    assert.equal(page.document.querySelector('[data-key-actions]').hidden, false);
    assert.equal(page.document.querySelector('[data-load-models]').hidden, false);
    assert.match(page.document.querySelector('[data-key-label]').textContent, /Replace/);
    field(page, 'api_key').value = 'new-key';
    await page.submit('.js-extractor-form'); await flush();
    assert.equal(page.calls[0][0], 'http://localhost/token/extractors/abc');
    assert.equal(page.calls[0][1].body.get('api_key'), null);
    assert.equal(page.calls[1][0], '/token/extractors/abc/key');
    assert.equal(page.calls[1][1].body.get('api_key'), 'new-key');
    assert.equal(dialogMessage(page).textContent, 'Model checked later. Kept for this session.');
    assert.equal(field(page, 'api_key').value, '');
  } finally { page.close(); }
});

test('editing a keyless endpoint offers no model list and keeps empty values', () => {
  const page = mount(extractorPage);
  try {
    page.document.querySelector('#extractor-loc [data-edit-extractor]').click();
    assert.equal(page.document.querySelector('[data-load-models]').hidden, true);
    assert.equal(page.document.querySelector('[data-key-actions]').hidden, true);
    assert.equal(field(page, 'context_length').value, '');
    assert.equal(field(page, 'base_url').value, 'http://h/v1');
  } finally { page.close(); }
});

test('a settings link to an extractor opens it for editing', () => {
  const page = mount(extractorPage, { url: 'http://localhost/token/settings#extractor-abc' });
  try {
    assert.equal(page.document.querySelector('#extractor-dialog').open, true);
    assert.equal(field(page, 'name').value, 'Router');
  } finally { page.close(); }
});

test('model suggestions load on request', async () => {
  for (const [response, ok, expected] of [
    [[{ id: 'a/<b>', name: 'A' }], true, /1 model available/],
    [[{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }], true, /2 models available/],
    [[], true, /no models/],
    [{ detail: 'Offline' }, false, /Offline/],
    [{}, false, /could not be loaded/],
  ]) {
    const page = mount(extractorPage, { ok, response });
    try {
      page.document.querySelector('#extractor-abc [data-edit-extractor]').click();
      page.document.querySelector('[data-load-models]').click(); await flush();
      assert.equal(page.calls[0][0], '/token/api/extractors/abc/models');
      assert.match(page.document.querySelector('[data-model-hint]').textContent, expected);
      if (ok && response.length) assert.equal(page.document.querySelectorAll('#extractor-model-options option').length, response.length);
      assert.equal(page.document.querySelector('[data-load-models]').disabled, false);
    } finally { page.close(); }
  }
});

test('choosing an OpenRouter model lists the providers serving it', async () => {
  for (const [response, ok, expected] of [
    [[{ tag: 'a/b', name: 'A' }, { tag: 'c', name: 'C' }], true, /2 providers serve/],
    [[{ tag: 'a/b', name: 'A' }], true, /1 provider serves/],
    [[], true, /No provider currently/],
    [{ detail: 'Down' }, false, /Down/],
    [{}, false, /could not be loaded/],
  ]) {
    const page = mount(extractorPage, { ok, response });
    try {
      page.document.querySelector('#extractor-abc [data-edit-extractor]').click();
      field(page, 'model').value = 'vendor/other';
      field(page, 'model').dispatchEvent(new page.window.Event('change')); await flush();
      assert.equal(page.calls[0][0], '/token/api/models/openrouter/endpoints?model=vendor%2Fother&extractor_id=abc');
      assert.match(page.document.querySelector('[data-openrouter-provider-status]').textContent, expected);
    } finally { page.close(); }
  }
  const page = mount(extractorPage);
  try {
    page.document.querySelector('[data-new-extractor]').click();
    field(page, 'model').dispatchEvent(new page.window.Event('change'));
    field(page, 'provider').value = 'openrouter';
    field(page, 'model').value = 'v/m';
    field(page, 'model').dispatchEvent(new page.window.Event('change')); await flush();
    assert.equal(page.calls.length, 1);
    assert.match(page.calls[0][0], /extractor_id=$/);
    field(page, 'model').value = ' ';
    field(page, 'model').dispatchEvent(new page.window.Event('change')); await flush();
    assert.equal(page.calls.length, 1);
  } finally { page.close(); }
});

test('removing a saved key asks first', async () => {
  for (const [confirm, ok, expected] of [[false, true, ''], [true, true, 'Key removed.'], [true, false, 'Locked']]) {
    const page = mount(extractorPage, { confirm, ok, response: ok ? {} : { detail: 'Locked' } });
    try {
      page.document.querySelector('#extractor-abc [data-edit-extractor]').click();
      page.document.querySelector('[data-remove-key]').click(); await flush();
      assert.equal(page.calls.length, confirm ? 1 : 0);
      if (confirm) assert.equal(page.calls[0][0], '/token/extractors/abc/key/delete');
      assert.equal(dialogMessage(page).textContent, expected);
      assert.match(page.confirmations[0], /Router/);
    } finally { page.close(); }
  }
});

test('duplicating opens the copy for editing and reports failures', async () => {
  for (const ok of [true, false]) {
    const page = mount(extractorPage, { ok, response: ok ? { extractor: { id: 'copy' } } : { detail: 'Gone' } });
    try {
      await page.submit('#extractor-abc .js-extractor-action');
      assert.equal(page.calls[0][0], 'http://localhost/token/extractors/abc/duplicate');
      assert.equal(page.timers.length > 0, ok);
      assert.equal(page.window.location.hash, ok ? '#extractor-copy' : '');
      const message = page.document.querySelector('[data-extractor-list-message]');
      assert.equal(message.textContent, ok ? '' : 'Gone');
      assert.ok(message.classList.contains('keep'));
    } finally { page.close(); }
  }
});

test('deleting an extractor confirms and names the projects that used it', async () => {
  for (const [confirm, ok, response, expected, state] of [
    [false, true, {}, '', null],
    [true, true, { projects: [] }, 'Deleted “Router”.', 'u-success'],
    [true, true, { projects: ['Study A', 'Study B'], warning: 'Key kept.' }, 'Deleted “Router”. Key kept. Choose another extractor for: Study A, Study B.', 'u-warning'],
    [true, true, {}, 'Deleted “Router”.', 'u-success'],
    [true, false, { detail: 'Gone' }, 'Gone', 'u-error'],
  ]) {
    const page = mount(extractorPage, { confirm, ok, response });
    try {
      await page.submit('#extractor-abc .js-delete-extractor');
      assert.equal(page.calls.length, confirm ? 1 : 0);
      const message = page.document.querySelector('[data-extractor-list-message]');
      assert.equal(message.textContent, expected);
      if (state) assert.ok(message.classList.contains(state));
      if (confirm) assert.match(page.confirmations[0], /Router/);
    } finally { page.close(); }
  }
});

test('extractor actions work without a list message or a dialog', async () => {
  const page = mount(`<form class="js-extractor-action" action="/token/extractors/x/duplicate"></form><form class="js-delete-extractor" action="/token/extractors/x/delete"></form>`, { ok: false, response: { detail: 'Gone' } });
  try {
    await page.submit('.js-extractor-action');
    await page.submit('.js-delete-extractor');
    assert.match(page.confirmations[0], /this extractor/);
    assert.equal(page.browserErrors.length, 0);
  } finally { page.close(); }
});
