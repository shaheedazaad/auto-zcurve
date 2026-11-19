const test = require('node:test');
const assert = require('node:assert/strict');
const { projectMount, flush } = require('./project-harness.cjs');
const project = { articles: [], pdf_count: 3, total_tokens: 42, successful_count: 1, failed_count: 1 };

test('run streams progress, shows slow-extraction warning, cancels, and handles terminal states', async t => {
  for (const status of ['complete', 'failed', 'cancelled']) {
    const h = projectMount('', { project }); t.after(h.close); await flush();
    assert.match(h.document.querySelector('.js-run').textContent, /Continue processing 2 articles/);
    await h.click('.js-run');
    assert.equal(h.calls.at(-1)[0], '/token/projects/project/run');
    assert.equal(h.calls.at(-1)[1].body.get('extractor_id'), 'lab');
    assert.equal(h.sources[0].url, '/token/projects/project/events');
    assert.equal(h.document.querySelector('.js-run').disabled, true);
    assert.equal(h.document.querySelector('.progress-area').hidden, false);
    await h.sources[0].emit('progress', { completed: 0, total: 0, message: 'Starting' });
    assert.equal(h.document.querySelector('[data-progress-bar]').getAttribute('aria-valuenow'), '0');
    h.timers[0]();
    assert.equal(h.document.querySelector('[data-slow-extraction-warning]').hidden, false);
    await h.sources[0].emit('progress', { completed: 1, total: 3, message: 'One done' });
    assert.equal(h.document.querySelector('[data-progress-bar] span').style.width, '33%');
    assert.equal(h.document.querySelector('[data-progress-count]').textContent, '1 / 3');
    assert.equal(h.document.querySelector('[data-slow-extraction-warning]').hidden, true);
    await h.sources[0].emit('status', { status: 'running', message: 'Working' });
    assert.equal(h.sources[0].closed, false);
    await h.click('.js-cancel');
    assert.equal(h.document.querySelector('.js-cancel').disabled, true);
    assert.equal(h.document.querySelector('[data-progress-message]').textContent, 'Cancelling…');
    await h.sources[0].emit('status', { status, message: 'Finished' });
    assert.equal(h.sources[0].closed, true);
    assert.equal(h.document.querySelector('.progress-area').hidden, true);
    assert.equal(h.document.querySelector('.run-message').textContent, 'Finished');
    assert.ok(h.document.querySelector('.run-message').classList.contains(status === 'complete' ? 'u-success' : 'u-error'));
    assert.equal(h.clearedIntervals.length, 1);
    assert.equal(h.timers.length, status === 'complete' ? 2 : 1);
  }
});

test('retry and report regeneration use their routes; active pages reconnect and poll', async t => {
  const h = projectMount('', { project, setup: window => { window.document.querySelector('.progress-area').hidden = false; } });
  t.after(h.close); await flush();
  assert.equal(h.sources.length, 1);
  await h.intervals[0](); await flush();
  assert.equal(h.calls.at(-1)[0], '/token/api/projects/project');
  await h.sources[0].emit('status', { status: 'cancelled', message: 'Cancelled' });
  await h.click('.js-retry');
  assert.equal(h.calls.at(-1)[0], '/token/projects/project/retry');
  assert.equal(h.sources.length, 2); assert.equal(h.sources[0].closed, true);
  await h.sources[1].emit('status', { status: 'complete', message: 'Done' });
  await h.click('.js-regenerate-report');
  assert.equal(h.calls.at(-1)[0], '/token/projects/project/regenerate-report');
  assert.equal([...h.calls.at(-1)[1].body.entries()].length, 0);
});

test('failed actions display errors', async t => {
  const h = projectMount('', { project, fetch: async (url, request) => request?.method === 'POST' ? { ok: false, json: async () => ({ detail: 'Request rejected' }) } : undefined });
  t.after(h.close); await flush();
  await h.click('.js-run');
  assert.equal(h.document.querySelector('.run-message').textContent, 'Request rejected');
  assert.equal(h.sources.length, 0);
  h.select('local'); await flush();
  assert.equal(h.document.querySelector('.project-settings-message').textContent, 'Request rejected');
  assert.ok(h.document.querySelector('.project-settings-message').classList.contains('u-error'));
  await h.click('.js-cancel');
  assert.equal(h.document.querySelector('.run-message').textContent, 'Request rejected');
  await h.click('.js-open-folder'); assert.deepEqual(h.alerts, ['Request rejected']);
});

test('choosing an extractor saves it, shows its settings, and gates runs on its key', async t => {
  const h = projectMount('', { project }); t.after(h.close); await flush();
  assert.equal(h.document.querySelector('.js-run').disabled, false);
  assert.equal(h.document.querySelector('[data-run-credential-prompt]').hidden, true);
  h.select('nokey'); await flush();
  const saved = h.calls.find(([url]) => url === '/token/projects/project/settings');
  assert.equal(saved[1].body.get('extractor_id'), 'nokey');
  assert.match(h.document.querySelector('.project-settings-message').textContent, /will use this extractor/);
  assert.equal(h.document.querySelector('.js-run').disabled, true);
  assert.equal(h.document.querySelector('[data-run-credential-prompt]').hidden, false);
  assert.equal(h.document.querySelector('[data-run-actions]').hidden, true);
  assert.equal(h.document.querySelector('[data-extractor-summary=nokey]').hidden, false);
  assert.equal(h.document.querySelector('[data-extractor-summary=lab]').hidden, true);
  assert.equal(h.document.querySelector('[data-extractor-deleted]').hidden, true);
  assert.equal(h.document.querySelector('[data-edit-extractor-link]').getAttribute('href'), '/token/settings#extractor-nokey');
  assert.equal(h.document.querySelector('[data-run-model-prompt]').hidden, true);
  for (const link of h.document.querySelectorAll('[data-setup-extractor-link]')) assert.equal(link.getAttribute('href'), '/token/settings#extractor-nokey');
  h.select('nomodel'); await flush();
  for (const link of h.document.querySelectorAll('[data-setup-extractor-link]')) assert.equal(link.getAttribute('href'), '/token/settings#extractor-nomodel');
  assert.equal(h.document.querySelector('.js-run').disabled, true);
  assert.equal(h.document.querySelector('[data-run-model-prompt]').hidden, false);
  assert.equal(h.document.querySelector('[data-run-credential-prompt]').hidden, true);
  h.select('local'); await flush();
  assert.equal(h.document.querySelector('.js-run').disabled, false);
});

test('a project without a chosen extractor cannot run', async t => {
  const h = projectMount('', { project, setup: window => {
    const select = window.document.querySelector('#extractor');
    select.insertAdjacentHTML('afterbegin', '<option value="" selected disabled>Choose</option>');
    select.value = '';
  } });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('.js-run').disabled, true);
  assert.equal(h.document.querySelector('[data-run-credential-prompt]').hidden, true);
  assert.equal(h.document.querySelector('[data-run-actions]').hidden, true);
});

test('opening the project folder gives temporary feedback', async t => {
  const h = projectMount(); t.after(h.close); await flush(); await h.click('.js-open-folder');
  assert.equal(h.calls.at(-1)[0], '/token/projects/project/open-folder');
  assert.equal(h.document.querySelector('.js-open-folder').textContent, 'Folder opened');
  h.timers[0](); assert.equal(h.document.querySelector('.js-open-folder').textContent, 'Open folder');
});

test('polling and extractor changes keep Run and Retry disabled during an active job', async t => {
  const h = projectMount('', { project, setup: window => {
    window.document.querySelector('.progress-area').hidden = false;
  } });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('.js-run').disabled, true);
  assert.equal(h.document.querySelector('.js-retry').disabled, true);
  await h.intervals[0](); await flush();
  h.select('local'); await flush();
  assert.equal(h.document.querySelector('.js-run').disabled, true);
  assert.equal(h.document.querySelector('.js-retry').disabled, true);
  await h.sources[0].emit('status', { status: 'cancelled', message: 'Stopped' });
  assert.equal(h.document.querySelector('.js-run').disabled, false);
  assert.equal(h.document.querySelector('.js-retry').disabled, false);
});

test('completed report reloads after showing success', async t => {
  const h = projectMount('', { project }); t.after(h.close); await flush();
  await h.click('.js-regenerate-report');
  await h.sources[0].emit('status', { status: 'complete', message: 'Report ready' });
  assert.equal(h.document.querySelector('.run-message').textContent, 'Report ready');
  assert.equal(h.browserErrors.length, 0);
  h.timers[0](); assert.match(h.browserErrors[0].message, /navigation/i);
});

test('a dropped progress stream shows a retrying message that clears when events resume', async t => {
  const h = projectMount('', { project, setup: window => { window.document.querySelector('.progress-area').hidden = false; } });
  t.after(h.close); await flush();
  const notice = h.document.querySelector('[data-connection-lost]');
  assert.equal(notice.hidden, true);
  h.sources[0].fail(h.window.EventSource.CONNECTING);
  assert.equal(notice.hidden, false);
  assert.match(h.document.querySelector('[data-connection-lost-title]').textContent, /Connection lost/);
  assert.match(h.document.querySelector('[data-connection-lost-message]').textContent, /Retrying/);
  await h.sources[0].emit('progress', { completed: 1, total: 2, message: 'Back' });
  assert.equal(notice.hidden, true);
  h.sources[0].fail(h.window.EventSource.CONNECTING);
  assert.equal(notice.hidden, false);
  await h.sources[0].emit('status', { status: 'running', message: 'Working' });
  assert.equal(notice.hidden, true);
  assert.equal(h.document.querySelector('[data-progress-message]').textContent, 'Working');
});

test('a progress stream that cannot reconnect tells the user to reload or restart', async t => {
  for (const failures of [[2], [0, 0, 0]]) {
    const h = projectMount('', { project, setup: window => { window.document.querySelector('.progress-area').hidden = false; } });
    t.after(h.close); await flush();
    for (const state of failures) h.sources[0].fail(state);
    const notice = h.document.querySelector('[data-connection-lost]');
    assert.equal(notice.hidden, false);
    assert.match(h.document.querySelector('[data-connection-lost-message]').textContent, /Reload this page.*restart auto-zcurve/s);
    assert.equal(h.document.querySelector('.progress-area').hidden, false);
    await h.sources[0].emit('status', { status: 'failed', message: 'Stopped' });
    assert.equal(notice.hidden, true);
  }
});
