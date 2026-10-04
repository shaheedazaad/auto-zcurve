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

test('credential saving reports secure, session-only, warning, and failure outcomes', async () => {
  for (const [options, expected, state] of [
    [{ response: { saved: true } }, 'Saved securely.', 'u-success'],
    [{ response: { saved: false } }, 'Key is ready for this session.', 'u-success'],
    [{ response: { warning: 'Keychain locked' } }, 'Keychain locked', 'u-warning'],
    [{ ok: false, response: { detail: 'Invalid key' } }, 'Invalid key', 'u-error'],
  ]) {
    const page = mount(keyForm, options);
    try {
      await page.submit('form');
      const message = page.document.querySelector('.form-message');
      assert.equal(message.textContent, expected);
      assert.equal(message.classList.contains(state), true);
      assert.equal(message.classList.contains('layout-class'), true);
      assert.equal(page.document.querySelector('[name=api_key]').value, options.ok === false ? 'secret' : '');
      assert.equal(page.timers.length, options.ok === false ? 0 : 1);
    } finally { page.close(); }
  }
});

test('key dialogs open, focus the input, and close', () => {
  const page = mount('<button data-open-key-dialog="gemini">Open</button><button id="missing" data-open-key-dialog="missing">Missing</button><dialog id="key-dialog-gemini"><input name="api_key"><button data-close-key-dialog>Close</button></dialog>');
  try {
    page.document.querySelector('[data-open-key-dialog]').click();
    const dialog = page.document.querySelector('dialog');
    assert.equal(dialog.open, true);
    assert.equal(page.document.activeElement, dialog.querySelector('input'));
    page.document.querySelector('[data-close-key-dialog]').click();
    assert.equal(dialog.open, false);
    page.document.querySelector('#missing').click();
  } finally { page.close(); }
});

test('unlock and removal report request outcomes through enclosing status element', async () => {
  for (const action of ['load', 'delete']) {
    for (const failed of [false, true]) {
      const page = mount(`<div data-credential-row><form class="js-${action}-credentials" action="/token/credentials/${action}" data-provider-label="Gemini"><input name="provider" value="gemini"></form><p class="form-message retained"></p></div>`, { ok: !failed, response: failed ? { detail: 'Unavailable' } : {} });
      try {
        await page.submit('form');
        assert.equal(page.calls.length, 1);
        assert.equal(page.document.querySelector('.form-message').textContent, failed ? 'Unavailable' : action === 'load' ? 'Saved key unlocked for this session.' : 'Key removed.');
        assert.equal(page.document.querySelector('.form-message').classList.contains('retained'), true);
        if (action === 'delete') assert.match(page.confirmations[0], /Gemini/);
      } finally { page.close(); }
    }
  }
});

test('declining key removal does not send a request', async () => {
  const page = mount('<form class="js-delete-credentials"><p class="form-message"></p></form>', { confirm: false });
  try {
    await page.submit('form');
    assert.equal(page.calls.length, 0);
  } finally { page.close(); }
});

test('settings navigation marks current location and expands target provider', () => {
  const page = mount('<nav class="settings-nav"><a href="#provider-connections">Connections</a><a href="#gemini">Gemini</a></nav><details class="provider-settings" id="gemini"></details>');
  try {
    const links = page.document.querySelectorAll('a');
    assert.equal(links[0].getAttribute('aria-current'), 'location');
    links[1].click();
    assert.equal(page.document.querySelector('details').open, true);
    page.window.location.hash = '#gemini';
    page.window.dispatchEvent(new page.window.Event('hashchange'));
    assert.equal(links[1].getAttribute('aria-current'), 'location');
    assert.equal(links[0].hasAttribute('aria-current'), false);
  } finally { page.close(); }
});

test('successful credential changes schedule a page refresh', async t => {
  for (const action of ['credentials', 'load-credentials', 'delete-credentials']) {
    const page = mount(`<form class="js-${action}" action="/credentials"><input name="provider" value="gemini"><input name="api_key" value="secret"><p class="form-message"></p></form>`);
    t.after(page.close);
    await page.submit('form');
    assert.equal(page.timers.length, 1);
    assert.equal(page.browserErrors.length, 0);
    page.timers[0]();
    assert.equal(page.browserErrors.length, 1);
    assert.match(page.browserErrors[0].message, /navigation/i);
  }
});

test('legacy credential rows receive status outside their form', async t => {
  const page = mount('<div class="row"><form class="js-load-credentials"><input name="provider" value="gemini"></form><p class="form-message layout"></p></div>');
  t.after(page.close); await page.submit('form');
  assert.equal(page.document.querySelector('.form-message').textContent, 'Saved key unlocked for this session.');
  assert.ok(page.document.querySelector('.form-message').classList.contains('layout'));
});
