const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
class Element {
    constructor(tag) { this.tagName = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.dataset = {}; this.value = ''; }
    append(...children) { this.children.push(...children); }
    replaceChildren(...children) { this.children = children; }
    setAttribute(key, value) { this.attributes[key] = value; if (key === 'value') this.value = value; }
    removeAttribute(key) { delete this.attributes[key]; }
    focus() {}
    addEventListener(name, fn) { this.listeners[name] = fn; }
    get textContent() { return (this.text || '') + this.children.map(c => c.textContent || '').join(''); }
    set textContent(value) { this.text = value; }
}
const elements = new Map(['discoverView', 'catalogView', 'settingsView', 'navSettings', 'hubSearchInput', 'hubModalBackdrop', 'hubModalTitle', 'hubModalBody', 'hubModalClose'].map(id => [id, new Element('div')]));
const document = {getElementById: id => elements.get(id), body: new Element('body'), querySelectorAll: () => []};
const calls = [], opened = [];
let settingsOpened = 0; let observer, observed = false, failPage = true;
const catalogs = [
    {key: 'popular', kind: 'movie', name: 'Popular', source: 'Cinemeta', genres: ['Drama'], genre_filter: true, search: true, pagination: true, required_filters: []},
    {key: 'new', kind: 'movie', name: 'New', source: 'Cinemeta', genres: ['2026', '2025'], genre_filter: true, required_filters: ['genre']},
    {key: 'anime', kind: 'anime', name: 'Anime', source: 'Anime addon', genres: [], required_filters: []},
];
const root = {IntersectionObserver: class {constructor(callback) {observer = callback;} observe() {observed = true;} disconnect() {observed = false;}}, StreamSettings: {open() { settingsOpened++; }}, open: url => opened.push(url), api: async url => {
    calls.push(url);
    if (url === '/api/stremio/addons') return {addons: [{key: 'safe', name: 'Addon', url: 'https://secret.invalid/token/manifest.json'}]};
    if (url === '/api/discover/catalogs') return {catalogs};
    if (url.includes('catalog_key=anime')) return {items: [{id: 'kitsu:1', kind: 'anime', title: 'Anime', playable: false, stremio_url: 'stremio:///detail/anime/kitsu%3A1'}]};
    if (url.includes('catalog_key=popular')) {
        const skip = new URLSearchParams(url.split('?')[1]).get('skip') || '0';
        if (skip === '2' && failPage) {failPage = false; throw new Error('temporary page failure');}
        const items = skip === '0' ? [{id:'one', kind:'movie', title:'One'}, {id:'two', kind:'movie', title:'Two'}] : skip === '2' ? [{id:'two', kind:'movie', title:'Two'}, {id:'three', kind:'movie', title:'Three'}] : [];
        return {items, next_skip: Number(skip) + items.length, has_more: items.length > 0};
    }
    return {items: []};
}};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../assets/hub.js'), 'utf8'), {window: root, document, URLSearchParams});
document.createElement = tag => new Element(tag);
const settle = async () => { for (let n = 0; n < 8; n++) await new Promise(resolve => setImmediate(resolve)); };
const walk = node => [node, ...node.children.flatMap(walk)];
const select = label => walk(elements.get('discoverView')).find(n => n.tagName === 'select' && n.attributes['aria-label'] === label);
(async () => {
    root.setHubView('settings');
    assert.equal(settingsOpened, 1);
    assert.equal(elements.get('settingsView').hidden, false);
    root.setHubView('discover'); await settle();
    assert.equal(elements.get('settingsView').hidden, true);
    assert.equal(walk(elements.get('discoverView')).filter(n => n.tagName === 'select').length, 3);
    assert.deepEqual(select('Catalog').children.map(n => n.value), ['popular', 'new']);
    const cards = () => walk(elements.get('discoverView')).filter(n => n.className === 'media-card');
    assert.equal(cards().length, 2);
    root.setHubView('settings'); root.setHubView('discover'); await settle();
    assert.ok(observed, 'reentering cached Discover reconnects automatic paging');
    elements.get('hubSearchInput').value = 'new pending search';
    observer([{isIntersecting:true}]); await settle();
    assert.ok(!calls.at(-1).includes('q='), 'append uses the loaded query until search debounce resets results');
    elements.get('hubSearchInput').value = '';
    assert.equal(cards().length, 2, 'page error retains existing titles');
    const retry = walk(elements.get('discoverView')).find(n => n.tagName === 'button' && n.textContent === 'Retry');
    await retry.listeners.click(); await settle();
    assert.equal(cards().length, 3, 'retry appends and deduplicates overlapping provider pages');
    assert.ok(calls.at(-1).includes('skip=2'));
    observer([{isIntersecting:true}]); await settle();
    assert.equal(cards().length, 3);
    assert.ok(!walk(elements.get('discoverView')).some(n => n.textContent === 'Load more'), 'empty last page stops pagination');

    const catalog = select('Catalog'); catalog.value = 'new'; await catalog.listeners.change({target: catalog}); await settle();
    assert.ok(calls.at(-1).includes('genre=2026'), 'New requires a real declared year');
    const type = select('Type'); type.value = 'anime'; await type.listeners.change({target: type}); await settle();
    const card = walk(elements.get('discoverView')).find(n => n.className === 'media-card');
    await card.listeners.click();
    assert.deepEqual(opened, ['stremio:///detail/anime/kitsu%3A1']);
    assert.ok(!calls.some(url => url.includes('/api/imdb/meta')), 'Anime must not be relabeled movie');
    await root.openAddonsModal();
    const modal = elements.get('hubModalBody');
    assert.ok(walk(modal).some(n => n.tagName === 'input' && n.attributes['aria-label'] === 'Manifest URL'));
    assert.ok(!modal.textContent.includes('secret.invalid'), 'raw addon URL must never be displayed');
    console.log('Discovery UI: catalog selectors, required year, settings navigation and external types verified.');
})().catch(error => { console.error(error); process.exitCode = 1; });
