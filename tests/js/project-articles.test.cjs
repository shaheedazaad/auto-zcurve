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
  assert.equal(h.document.querySelector('[data-pdf-count]').textContent, '12 PDFs');
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
      assert.equal(h.document.querySelector('.upload-message').textContent, ok ? `Added ${count} PDF${count === 1 ? '' : 's'}.` : 'Invalid PDF');
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
  assert.match(h.document.querySelector('.js-run').textContent, /Continue processing 1 PDF/);
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
