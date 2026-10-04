const test = require('node:test');
const assert = require('node:assert/strict');
const { projectMount, flush } = require('./project-harness.cjs');
const html = `<button data-open-rename-dialog>Rename</button><dialog id="rename-project-dialog"><button data-close-rename-dialog>Close</button><form class="js-rename-project" action="/rename"><input name="name" value="Original"><button type="submit">Save</button><p class="form-message layout"></p></form></dialog><form class="js-reset-project" action="/reset"></form><form class="js-delete-project" action="/delete" data-project-name="My project"></form>`;

test('rename dialog focuses and selects the name, saves, and recovers from errors', async t => {
  for (const ok of [true, false]) {
    const h = projectMount(html, { fetch: async (url, request) => request?.method === 'POST' ? { ok, json: async () => ({ detail: 'Name rejected' }) } : undefined });
    t.after(h.close); await flush(); await h.click('[data-open-rename-dialog]');
    const input = h.document.querySelector('[name=name]');
    assert.equal(h.document.activeElement, input); assert.equal(input.selectionStart, 0); assert.equal(input.selectionEnd, 8);
    assert.equal(h.document.querySelector('dialog').open, true);
    await h.click('[data-close-rename-dialog]'); assert.equal(h.document.querySelector('dialog').open, false);
    h.input('[name=name]', 'Updated'); await h.submit('.js-rename-project');
    assert.equal(h.calls.at(-1)[1].body.get('name'), 'Updated');
    if (ok) {
      assert.equal(h.browserErrors.length, 1); assert.match(h.browserErrors[0].message, /navigation/i);
    } else {
      assert.equal(h.document.querySelector('.js-rename-project button').disabled, false);
      assert.equal(h.document.querySelector('.form-message').textContent, 'Name rejected');
      assert.ok(h.document.querySelector('.form-message').classList.contains('layout'));
    }
  }
});

for (const action of ['reset', 'delete']) {
  test(`${action} requires confirmation and handles success and rejection`, async t => {
    for (const [confirm, ok] of [[false, true], [true, true], [true, false]]) {
      const h = projectMount(html, { confirm, fetch: async (url, request) => request?.method === 'POST' ? { ok, json: async () => ({ detail: 'Action rejected' }) } : undefined });
      t.after(h.close); await flush(); await h.submit(`.js-${action}-project`);
      const posts = h.calls.filter(([, request]) => request?.method === 'POST');
      assert.equal(posts.length, Number(confirm));
      assert.match(h.confirmations[0], action === 'delete' ? /My project/ : /PDFs, instructions, and the extraction schema will be kept/);
      if (confirm && !ok) assert.deepEqual(h.alerts, ['Action rejected']);
      if (confirm && ok) { assert.equal(h.browserErrors.length, 1); assert.match(h.browserErrors[0].message, /navigation/i); }
    }
  });
}

test('deleting a project without a display name uses a clear fallback confirmation', async t => {
  const h = projectMount('<form class="js-delete-project" action="/delete"></form>', { confirm: false });
  t.after(h.close); await flush(); await h.submit('.js-delete-project');
  assert.match(h.confirmations[0], /this project/);
  assert.equal(h.calls.filter(([, request]) => request?.method === 'POST').length, 0);
});
