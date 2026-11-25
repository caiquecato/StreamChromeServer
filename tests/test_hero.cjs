const assert = require('node:assert/strict');
const {harness, settle} = require('./test_hub.cjs');
(async () => {
    const home = {movies: [{id:'one', title:'One'}, {id:'two', title:'Two'}], series: [], hero_items: [
        {id:'one', title:'One', poster_url:'/official.jpg', backdrop_url:'/frame.jpg'},
        {id:'two', title:'Two', poster_url:'/second.jpg'},
    ], featured: {id:'one'}};
    const h = harness({home}); await settle();
    const nodes = () => h.elements.get('catalogView');
    const button = label => nodes().querySelectorAll('button').find(n => n.attributes['aria-label'] === label);
    assert.equal(nodes().querySelectorAll('img')[0].attributes.src, '/official.jpg');
    await button('Next feature').emit('click');
    assert.ok(nodes().textContent.includes('2 / 2'));
    await button('Previous feature').emit('click');
    assert.ok(nodes().textContent.includes('1 / 2'));
    const pause = button('Pause carousel');
    assert.equal(pause.querySelectorAll('svg').length, 1, 'pause is an explicit SVG action');
    await pause.emit('click');
    assert.ok(button('Resume carousel'));
    assert.equal(h.timers.size, 0, 'paused carousel cannot schedule advancement');
    await button('Resume carousel').emit('click');
    assert.equal(h.timers.size, 1);
    const feature = nodes().querySelectorAll('.hub-featured')[0]; feature.focus();
    const fire = () => {const [id, fn] = h.timers.entries().next().value; h.timers.delete(id); fn();};
    fire(); assert.ok(nodes().textContent.includes('1 / 2'), 'focus suspends autoplay');
    h.document.body.focus(); fire();
    assert.ok(nodes().textContent.includes('2 / 2'), 'autoplay resumes after focus leaves');
    console.log('Hero navigation, pause/resume SVG, focus suspension and official artwork verified.');
})().catch(error => {console.error(error); process.exitCode=1;});
