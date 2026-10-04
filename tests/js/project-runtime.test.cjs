const test = require('node:test');
const assert = require('node:assert/strict');
const { projectMount, flush } = require('./project-harness.cjs');
const project = { articles: [], pdf_count: 3, total_tokens: 42, successful_count: 1, failed_count: 1 };

test('run streams progress, shows slow-extraction warning, cancels, and handles terminal states', async t => {
  for (const status of ['complete', 'failed', 'cancelled']) {
    const h = projectMount('', { project }); t.after(h.close); await flush();
    h.select('openai_compatible'); await flush();
    assert.match(h.document.querySelector('.js-run').textContent, /Continue processing 2 PDFs/);
    await h.click('.js-run');
    assert.equal(h.calls.at(-1)[0], '/token/projects/project/run');
    assert.equal(h.calls.at(-1)[1].body.get('provider'), 'openai_compatible');
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
  h.select('openai_compatible'); await flush(); await h.click('.js-retry');
  assert.equal(h.calls.at(-1)[0], '/token/projects/project/retry');
  assert.equal(h.sources.length, 2); assert.equal(h.sources[0].closed, true);
  await h.sources[1].emit('status', { status: 'complete', message: 'Done' });
  await h.click('.js-regenerate-report');
  assert.equal(h.calls.at(-1)[0], '/token/projects/project/regenerate-report');
  assert.equal([...h.calls.at(-1)[1].body.entries()].length, 0);
});

test('invalid settings stop a run, and failed actions display errors', async t => {
  const h = projectMount('', { project, fetch: async (url, request) => request?.method === 'POST' ? { ok: false, json: async () => ({ detail: 'Request rejected' }) } : undefined });
  t.after(h.close); await flush(); h.select('openai_compatible'); await flush();
  const form = h.document.querySelector('.js-run-settings'); form.reportValidity = () => false;
  await h.click('.js-run'); await h.click('.js-save-project-settings');
  assert.equal(h.calls.filter(([, request]) => request?.method === 'POST').length, 0);
  form.reportValidity = () => true;
  await h.click('.js-run');
  assert.equal(h.document.querySelector('.run-message').textContent, 'Request rejected');
  assert.equal(h.sources.length, 0);
  await h.click('.js-save-project-settings');
  assert.equal(h.document.querySelector('.project-settings-message').textContent, 'Request rejected');
  await h.click('.js-cancel');
  assert.equal(h.document.querySelector('.run-message').textContent, 'Request rejected');
  await h.click('.js-open-folder'); assert.deepEqual(h.alerts, ['Request rejected']);
});

test('provider switching updates credential requirements and preserves edited concurrency', async t => {
  const h = projectMount('', { project }); t.after(h.close); await flush();
  assert.equal(h.document.querySelector('.js-run').disabled, true);
  assert.equal(h.document.querySelector('[data-run-credential-prompt]').hidden, false);
  assert.match(h.document.querySelector('#model').placeholder, /Add an API key/);
  h.select('openrouter'); await flush();
  assert.equal(h.document.querySelector('#model').value, 'vendor/test');
  assert.equal(h.document.querySelector('#parallel-requests').value, '1');
  assert.equal(h.document.querySelector('#request-delay-sec').value, '0');
  assert.equal(h.document.querySelector('[data-openrouter-warning]').hidden, false);
  assert.equal(h.document.querySelector('.js-run').disabled, false);
  assert.equal(h.document.querySelector('[data-macos-key-note]').hidden, false);
  h.input('#parallel-requests', '7'); h.input('#request-delay-sec', '3');
  h.select('openai_compatible'); await flush();
  assert.equal(h.document.querySelector('#parallel-requests').value, '7');
  assert.equal(h.document.querySelector('#request-delay-sec').value, '3');
  assert.match(h.document.querySelector('[data-model-hint]').textContent, /locally extracted text/);
  h.document.querySelector('#provider').dataset.endpointReady = 'false'; h.select('openai_compatible'); await flush();
  assert.equal(h.document.querySelector('.js-run').disabled, true);
  assert.match(h.document.querySelector('[data-key-heading]').textContent, /Configure your endpoint/);
  h.document.querySelector('[value=gemini]').dataset.savedKey = 'true'; h.select('gemini'); await flush();
  assert.match(h.document.querySelector('#model').placeholder, /first run/);
  assert.equal(h.document.querySelector('.js-run').disabled, false);
});

test('OpenRouter model validation gates settings and runs', async t => {
  for (const ok of [true, false]) {
    const h = projectMount('', { project, fetch: async url => url.endsWith('/validate') ? { ok, json: async () => ({ detail: 'Unsupported model' }) } : undefined });
    t.after(h.close); await flush(); h.select('openrouter'); await flush();
    h.document.querySelector('#model').dispatchEvent(new h.window.Event('blur')); await flush();
    assert.match(h.document.querySelector('[data-model-validation]').textContent, ok ? /Validated/ : /Unsupported model/);
    await h.click('.js-save-project-settings');
    assert.equal(h.calls.some(([url]) => url.endsWith('/settings')), ok);
    if (ok) assert.equal(h.document.querySelector('.project-settings-message').textContent, 'Project settings saved.');
    await h.click('.js-run'); assert.equal(h.sources.length, Number(ok));
  }
});

test('opening the project folder gives temporary feedback', async t => {
  const h = projectMount(); t.after(h.close); await flush(); await h.click('.js-open-folder');
  assert.equal(h.calls.at(-1)[0], '/token/projects/project/open-folder');
  assert.equal(h.document.querySelector('.js-open-folder').textContent, 'Folder opened');
  h.timers[0](); assert.equal(h.document.querySelector('.js-open-folder').textContent, 'Open folder');
});

test('polling and provider refresh keep Run and Retry disabled during an active job', async t => {
  const h = projectMount('', { project, setup: window => {
    window.document.querySelector('#provider').value = 'openai_compatible';
    window.document.querySelector('.progress-area').hidden = false;
  } });
  t.after(h.close); await flush();
  assert.equal(h.document.querySelector('.js-run').disabled, true);
  assert.equal(h.document.querySelector('.js-retry').disabled, true);
  await h.intervals[0](); await flush();
  h.select('openrouter'); await flush();
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
