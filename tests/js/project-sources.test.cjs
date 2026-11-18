const test = require('node:test');
const assert = require('node:assert/strict');
const { projectMount, flush } = require('./project-harness.cjs');

const sourcesHtml = `<form class="js-upload" action="/upload"><input type="file" multiple></form><p class="upload-message"></p>
<table><tbody data-source-body></tbody></table>
<nav data-source-pagination><span data-source-page-status></span><button class="js-source-previous">Previous</button><button class="js-source-next">Next</button></nav>`;
const sources = Array.from({ length: 12 }, (_, index) => ({
  name: `Paper ${String(index).padStart(2, '0')}.pdf`, status: 'ready',
  added_at: `2026-11-${String(index + 1).padStart(2, '0')}T10:00:00`, added_display: `${index + 1} Nov 2026, 10:00`, size_display: `${index + 1} KB`,
}));
const project = { articles: sources, pdf_count: 12, total_tokens: 0, successful_count: 0, failed_count: 0 };
const names = h => [...h.document.querySelectorAll('[data-source-body] .article-name')].map(node => node.textContent);

test('sources list uploaded PDFs newest first with size, date, and a paginated open link', async t => {
  const h = projectMount(sourcesHtml, { project }); t.after(h.close); await flush();
  assert.equal(names(h).length, 10); assert.equal(names(h)[0], 'Paper 11.pdf');
  const first = h.document.querySelector('[data-source-body] tr');
  assert.deepEqual([...first.querySelectorAll('td')].slice(1, 3).map(td => td.textContent), ['12 KB', '12 Nov 2026, 10:00']);
  assert.equal(first.querySelector('a').getAttribute('href'), '/token/projects/project/sources/Paper%2011.pdf');
  const status = () => h.document.querySelector('[data-source-page-status]').textContent;
  assert.equal(status(), 'Page 1 of 2'); assert.equal(h.document.querySelector('.js-source-previous').disabled, true);
  await h.click('.js-source-previous'); assert.equal(status(), 'Page 1 of 2');
  await h.click('.js-source-next'); assert.deepEqual(names(h), ['Paper 01.pdf', 'Paper 00.pdf']);
  assert.equal(status(), 'Page 2 of 2'); assert.equal(h.document.querySelector('.js-source-next').disabled, true);
  await h.click('.js-source-next'); assert.equal(status(), 'Page 2 of 2');
  await h.click('.js-source-previous'); assert.equal(status(), 'Page 1 of 2');
});

test('sources show an empty state, tolerate missing metadata, and escape names', async t => {
  const empty = projectMount(sourcesHtml); t.after(empty.close); await flush();
  assert.match(empty.document.querySelector('[data-source-body]').textContent, /No PDFs yet/);
  assert.equal(empty.document.querySelector('[data-source-pagination]').hidden, true);
  const odd = [{ name: 'b<img src=x>.pdf', status: 'ready' }, { name: 'a.pdf', status: 'ready' }];
  const h = projectMount('<table><tbody data-source-body></tbody></table>', { project: { ...project, articles: odd, pdf_count: 2 } });
  t.after(h.close); await flush();
  assert.deepEqual(names(h), ['a.pdf', 'b<img src=x>.pdf']);
  assert.equal(h.document.querySelector('[data-source-body] img'), null);
  assert.equal(h.document.querySelector('[data-source-body] td:nth-child(2)').textContent, '');
});

test('newly uploaded PDFs are highlighted and the list returns to the first page', async t => {
  const state = { ...project };
  const h = projectMount(sourcesHtml, {
    fetch: async (url, request) => {
      if (request?.method === 'POST') return { ok: true, json: async () => ({ saved: ['Fresh.pdf'] }) };
      if (url.includes('/api/projects/')) return { ok: true, json: async () => state };
    },
  });
  t.after(h.close); await flush();
  await h.click('.js-source-next');
  state.articles = [...sources, { name: 'Fresh.pdf', status: 'ready', added_at: '2026-12-01T09:00:00', added_display: '1 Dec 2026, 09:00', size_display: '3 KB' }];
  const drop = new h.window.Event('drop', { cancelable: true });
  Object.defineProperty(drop, 'dataTransfer', { value: { files: [new h.window.File(['pdf'], 'Fresh.pdf')] } });
  h.document.querySelector('.js-upload').dispatchEvent(drop); await flush(); await flush();
  assert.equal(names(h)[0], 'Fresh.pdf');
  const rows = h.document.querySelectorAll('[data-source-body] tr');
  assert.ok(rows[0].classList.contains('source-row-new')); assert.equal(rows[0].querySelector('.badge').textContent, 'New');
  assert.equal(rows[1].classList.contains('source-row-new'), false);
  assert.equal(h.document.querySelector('[data-source-page-status]').textContent, 'Page 1 of 2');
});
