const { test } = require('node:test');
const assert = require('node:assert/strict');
const { mount } = require('./harness.cjs');
const markup = '<button data-new-project-toggle>New</button><dialog id="new-project-dialog"><input name="name"><button data-close-new-project>Close</button></dialog><input class="js-project-search"><select id="project-sort"><option value="newest">Newest</option><option value="oldest">Oldest</option><option value="name">Name</option></select><span data-project-count></span><div data-project-list>Server content</div><div data-project-pagination><button class="js-project-previous">Previous</button><span data-project-page></span><button class="js-project-next">Next</button></div>';
const settle = () => new Promise(resolve => setImmediate(resolve));
function setup(response, options = {}) { return mount(markup, { attributes: 'data-token="token"', response, ...options }); }

test('project list paginates, searches case-insensitively, and sorts', async () => {
  const projects = Array.from({ length: 12 }, (_, i) => ({ id: String(i), name: `Study ${String(i).padStart(2, '0')}`, pdf_count: i, total_tokens: i * 10, failed_count: i === 1 ? 1 : 0, has_report: i === 2 }));
  const page = setup(projects);
  try {
    await settle();
    const rows = () => [...page.document.querySelectorAll('[data-project-row]')];
    assert.equal(rows().length, 10);
    assert.equal(page.document.querySelector('[data-project-count]').textContent, '12 projects');
    assert.match(rows()[1].textContent, /1 article/);
    assert.match(rows()[1].textContent, /1 failed/);
    assert.match(rows()[2].textContent, /Report ready/);
    page.document.querySelector('.js-project-next').click();
    assert.equal(rows().length, 2);
    assert.match(page.document.querySelector('[data-project-page]').textContent, /Page 2 of 2/);
    page.document.querySelector('.js-project-previous').click();
    assert.equal(rows().length, 10);
    const sort = page.document.querySelector('select');
    sort.value = 'oldest';
    sort.dispatchEvent(new page.window.Event('change'));
    assert.match(rows()[0].textContent, /Study 11/);
    sort.value = 'name';
    sort.dispatchEvent(new page.window.Event('change'));
    assert.match(rows()[0].textContent, /Study 00/);
    const search = page.document.querySelector('input.js-project-search');
    search.value = ' STUDY 11 ';
    search.dispatchEvent(new page.window.Event('input'));
    assert.equal(rows().length, 1);
    assert.equal(page.document.querySelector('[data-project-count]').textContent, '1 project');
    search.value = 'missing';
    search.dispatchEvent(new page.window.Event('input'));
    assert.match(page.document.querySelector('[data-project-list]').textContent, /No matching projects/);
    assert.equal(page.document.querySelector('[data-project-pagination]').hidden, true);
  } finally { page.close(); }
});

test('project names are rendered as text and IDs are encoded', async () => {
  const page = setup([{ id: '../x?y', name: '<img src=x onerror=alert(1)>', pdf_count: 0, total_tokens: 0 }]);
  try {
    await settle();
    assert.equal(page.document.querySelector('[data-project-list] img'), null);
    const row = page.document.querySelector('[data-project-row]');
    assert.match(row.textContent, /<img src=x/);
    assert.equal(row.getAttribute('href'), '/token/projects/..%2Fx%3Fy');
  } finally { page.close(); }
});

test('empty catalog shows guidance and failed fetch keeps server content', async () => {
  for (const options of [{}, { ok: false }, { reject: 'offline' }]) {
    const page = setup([], options);
    try {
      await settle();
      const text = page.document.querySelector('[data-project-list]').textContent;
      assert.match(text, options.ok === false || options.reject ? /Server content/ : /No projects yet/);
    } finally { page.close(); }
  }
});

test('new-project dialog opens with focused name and closes', () => {
  const page = setup([]);
  try {
    page.document.querySelector('[data-new-project-toggle]').click();
    const dialog = page.document.querySelector('dialog');
    assert.equal(dialog.open, true);
    assert.equal(page.document.activeElement, dialog.querySelector('input'));
    page.document.querySelector('[data-close-new-project]').click();
    assert.equal(dialog.open, false);
  } finally { page.close(); }
});
