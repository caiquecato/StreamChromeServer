const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// A small DOM double with real attachment/focus semantics. Tests drive public
// navigation and rendered controls; production private functions stay private.
function harness() {
    let document;
    class Node {
        constructor(tag) {
            this.tagName = tag.toUpperCase(); this.children = []; this.parentNode = null;
            this.attributes = {}; this.dataset = {}; this.listeners = new Map();
            this.className = ''; this.value = ''; this._text = ''; this.hidden = false;
            this.disabled = false; this.checked = false; this.inert = false;
            this.style = {setProperty() {}};
        }
        get isConnected() { return this === document.body || Boolean(this.parentNode?.isConnected); }
        contains(node) { return this === node || this.children.some(child => child.contains(node)); }
        get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
        set textContent(value) { this.replaceChildren(); this._text = String(value); }
        append(...children) {
            for (const child of children) {
                if (child.parentNode) child.parentNode.children.splice(child.parentNode.children.indexOf(child), 1);
                child.parentNode = this; this.children.push(child);
            }
        }
        replaceChildren(...children) {
            for (const child of this.children) {
                if (child.contains(document.activeElement)) document.activeElement = document.body;
                child.parentNode = null;
            }
            this.children = []; this._text = ''; this.append(...children);
        }
        setAttribute(name, value) {
            this.attributes[name] = String(value);
            if (name === 'class') this.className = value;
            if (name === 'value') this.value = value;
            if (name === 'hidden' || name === 'disabled') this[name] = true;
            if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
        }
        removeAttribute(name) { delete this.attributes[name]; if (name === 'disabled' || name === 'hidden') this[name] = false; }
        addEventListener(name, callback) { this.listeners.set(name, [...(this.listeners.get(name) || []), callback]); }
        async emit(name) {
            await Promise.all((this.listeners.get(name) || []).map(callback => callback({target: this, stopPropagation() {}, preventDefault() {}})));
        }
        focus() { if (this.isConnected && !this.disabled) document.activeElement = this; }
        matches(selector) {
            if (selector === ':hover, :focus-within') return this.contains(document.activeElement);
            if (selector.startsWith('.')) return this.className.split(' ').includes(selector.slice(1));
            const attribute = selector.match(/^\[([^\]]+)\]$/);
            if (attribute) return Object.hasOwn(this.attributes, attribute[1]);
            const tag = selector.match(/^([\w-]+)/)?.[1];
            return this.tagName.toLowerCase() === tag && (!selector.includes(':not(:disabled)') || !this.disabled);
        }
        querySelectorAll(selector) {
            const matches = []; const selectors = selector.split(',').map(value => value.trim());
            const visit = node => { for (const child of node.children) { if (selectors.some(value => child.matches(value))) matches.push(child); visit(child); } };
            visit(this); return matches;
        }
        scrollIntoView() {}
    }
    const elements = new Map();
    document = {
        createElement: tag => new Node(tag), createElementNS: (_, tag) => new Node(tag),
        createTextNode: text => { const node = new Node('#text'); node._text = text; return node; },
        getElementById: id => elements.get(id) || null,
        querySelectorAll: selector => document.body.querySelectorAll(selector),
        hidden: false,
    };
    document.body = new Node('body'); document.activeElement = document.body;
    for (const id of ['catalogView', 'discoverView', 'navCatalog', 'navDiscover', 'navPlayer', 'hubSearchInput',
        'hubModalBackdrop', 'hubModalTitle', 'hubModalBody', 'hubSubModalBackdrop', 'hubSubModalTitle',
        'hubSubModalBody', 'hubSubModalClose', 'mainVideo', 'nextEpisodeButton', 'downloadsBadge']) {
        const node = new Node(id.includes('Close') ? 'button' : 'div');
        elements.set(id, node); document.body.append(node);
    }
    for (const prefix of ['hubModal', 'hubSubModal']) {
        const backdrop = elements.get(prefix + 'Backdrop'); backdrop.hidden = true;
        backdrop.append(elements.get(prefix + 'Title'), elements.get(prefix + 'Body'));
    }
    elements.get('hubSubModalBackdrop').append(elements.get('hubSubModalClose'));
    const events = new Map(), calls = [], streamCounts = new Map();
    let failEpisodeTwo = true, position = 0;
    const episodes = [1, 2, 3].map(episode => ({id: `tt123:1:${episode}`, season: 1, episode, title: `Episode ${episode}`}));
    const root = {
        document, AppUI: {translate: text => text}, SeasonQueue: require('../assets/season_queue.js'),
        addEventListener(name, callback) { events.set(name, [...(events.get(name) || []), callback]); },
        dispatchEvent(event) { for (const callback of events.get(event.type) || []) callback(event); },
        getPlaybackPosition: () => position, getMovieDuration: () => 10800,
        async api(url, body) {
            calls.push({url, body});
            if (url === '/api/catalog') return {movies: [], series: [], continue_watching: []};
            if (url === '/api/library/downloads') return {jobs: []};
            if (url === '/api/discover/catalogs') return {catalogs: [{key: 'popular', kind: 'movie', name: 'Popular', source: 'Cinemeta', genres: [], genre_filter: false, search: true, required_filters: []}]};
            if (url.startsWith('/api/discover?')) return {items: [{kind: 'series', imdb_id: 'tt123', title: 'Test series'}]};
            if (url.startsWith('/api/imdb/meta?')) return {kind: 'series', imdb_id: 'tt123', title: 'Test series', seasons: [{season: 1, episodes}]};
            if (url.startsWith('/api/stremio/streams?')) {
                const id = new URL('http://local' + url).searchParams.get('id');
                const count = (streamCounts.get(id) || 0) + 1; streamCounts.set(id, count);
                const streams = ['A', 'B'].map(key => ({playable: true, source_key: `${id}-${key}`, stream_token: `${id}-${key}-${count}`, filename: `${key}.mkv`, name: key}));
                return {streams: count > 1 ? streams.reverse() : streams};
            }
            if (url === '/api/library/download') {
                if (body.episode === 2 && failEpisodeTwo) { failEpisodeTwo = false; throw new Error('temporary queue failure'); }
                return {id: 'job-' + body.episode};
            }
            if (url === '/api/catalog/open') return {source: 'local:' + body.id, item_id: body.id, filename: body.id};
            if (url === '/api/catalog/progress') return {};
            throw new Error('Unexpected API call: ' + url);
        },
        async loadDownloadedVideo(source) {
            root.dispatchEvent({type: 'streamchrome:source-changing'});
            position = 0;
            root.dispatchEvent({type: 'streamchrome:media', detail: {source}});
        },
    };
    const context = vm.createContext({window: root, document, console, URLSearchParams,
        setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {}});
    vm.runInContext(fs.readFileSync(path.join(__dirname, '../assets/hub.js'), 'utf8'), context);
    return {root, document, elements, calls, streamCounts, setPosition(value) { position = value; }};
}

async function settle() { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); }
const byText = (node, text) => node.querySelectorAll('button').find(button => button.textContent === text);

async function seriesFlow() {
    const h = harness(); await settle();
    h.root.setHubView('discover'); await settle();
    const card = h.elements.get('discoverView').querySelectorAll('.media-card')[0];
    assert.ok(card, 'Discover must render a clickable title');
    await card.emit('click'); await settle();
    const parent = h.elements.get('hubModalBackdrop'), body = h.elements.get('hubModalBody');
    assert.equal(parent.hidden, false);
    const episodeButton = byText(body, 'Load streams / subs');
    episodeButton.focus(); await episodeButton.emit('click'); await settle();
    assert.equal(h.elements.get('hubSubModalBackdrop').hidden, false);
    assert.equal(parent.inert, true, 'episode source overlay must disable the underlying series dialog');
    h.root.closeHubSubModal();
    assert.equal(parent.inert, false);
    const episodeReturnFocus = h.document.activeElement;

    const batch = byText(body, 'Download season 1');
    batch.focus(); await batch.emit('click'); await settle();
    const subbody = h.elements.get('hubSubModalBody');
    const selects = subbody.querySelectorAll('select'); assert.equal(selects.length, 3);
    for (let i = 0; i < 2; i++) {
        const selected = selects[i]; selected.focus();
        selected.value = selected.children.find(option => option.value.includes('-B-')).value;
        await selected.emit('change');
        assert.equal(subbody.querySelectorAll('select')[i], selected, 'select change must retain its DOM node');
        assert.equal(h.document.activeElement, selected, 'select change must retain keyboard focus');
    }
    const thirdCheck = subbody.querySelectorAll('input')[2];
    thirdCheck.checked = false; await thirdCheck.emit('change');
    const submit = subbody.querySelectorAll('button')[0];
    await submit.emit('click'); await settle();
    const downloads = () => h.calls.filter(call => call.url === '/api/library/download');
    assert.equal(downloads().length, 2, 'only episodes with a selected source should be submitted');
    assert.deepEqual(downloads().map(call => call.body.episode), [1, 2]);
    for (const call of downloads()) {
        assert.equal(call.body.filename, 'B.mkv', 'refresh must preserve chosen identity when source order changes');
        assert.match(call.body.stream_token, /-B-[23]$/, 'queue must use a newly fetched token');
    }
    assert.match(subbody.textContent, /temporary queue failure/, 'partial failure must remain visible');
    await submit.emit('click'); await settle();
    assert.deepEqual(downloads().map(call => call.body.episode), [1, 2, 2], 'retry must skip the successfully queued episode');
    assert.equal(h.streamCounts.get('tt123:1:3'), 1, 'unchecked episode must not refresh or queue');
    h.root.closeHubSubModal();
    assert.equal(h.document.activeElement, batch, 'batch overlay must restore its opener');
    assert.equal(episodeReturnFocus.isConnected, true, 'closing source overlay must restore connected focus');
    assert.equal(episodeReturnFocus.textContent, 'Load streams / subs', 'closing source overlay must return focus to its episode trigger');
}

async function progressFlow() {
    const h = harness(); await settle();
    await h.root.playCatalogItem('old-movie'); h.setPosition(3);
    await h.root.playCatalogItem('new-movie'); await settle();
    const saves = () => h.calls.filter(call => call.url === '/api/catalog/progress').map(call => call.body);
    assert.equal(saves().length, 1);
    assert.equal(saves()[0].id, 'old-movie', 'switch must save old position under old movie id');
    assert.equal(saves()[0].position, 3, 'three seconds must count even in a three-hour movie');
    assert.equal(saves()[0].duration, 10800);
    h.setPosition(17); await h.elements.get('mainVideo').emit('pause');
    assert.equal(saves().at(-1).id, 'new-movie');
    assert.equal(saves().at(-1).position, 17);
    h.root.dispatchEvent({type: 'streamchrome:source-changing'});
    h.root.dispatchEvent({type: 'streamchrome:media', detail: {source: 'https://example.org/manual.mkv'}});
    const previous = saves().length;
    h.setPosition(80); await h.elements.get('mainVideo').emit('pause');
    assert.equal(saves().length, previous, 'manual URL playback must not inherit the previous catalog movie');
}

(async () => {
    await progressFlow();
    await seriesFlow();
    console.log('Hub integration: progress ownership, nested dialogs, stable focus, exact source refresh and partial retry verified.');
})().catch(error => { console.error(error); process.exitCode = 1; });
