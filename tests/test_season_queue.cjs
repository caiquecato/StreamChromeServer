const assert = require('node:assert/strict');
const {loadSources, queueSelected, refreshedChoice} = require('../assets/season_queue.js');
(async () => {
    assert.equal(refreshedChoice({source_key:'exact'}, [{source_key:'different',playable:true}, {source_key:'exact',playable:true,stream_token:'fresh'}]).stream_token, 'fresh');
    assert.throws(() => refreshedChoice({source_key:'gone'}, [{source_key:'different',playable:true}]), /no longer available/);
    const rows = Array.from({length: 7}, (_, i) => ({ep: {id: String(i)}, selected: true}));
    let running = 0, peak = 0;
    await loadSources(rows, async id => {
        peak = Math.max(peak, ++running);
        await new Promise(resolve => setTimeout(resolve, 2));
        running--;
        if (id === '2') throw Error('Unavailable');
        return [{stream_token: id, playable: true}, {playable: false}];
    }, () => {}, () => true);
    assert.equal(peak, 3);
    assert.equal(rows[2].error, 'Unavailable');
    assert.equal(rows[0].streams.length, 1);
    assert.equal(rows[0].choice, '', 'Never silently choose a source');
    for (const row of rows) row.choice = row.streams?.[0]?.stream_token ?? '';
    rows[1].selected = false;
    const sent = [];
    await queueSelected(rows, async (stream, ep) => {
        if (ep.id === '4') throw Error('Failed');
        sent.push(stream.stream_token);
    }, () => {});
    assert.deepEqual(sent, ['0', '3', '5', '6']);
    assert.equal(rows[4].queueError, 'Failed');
    assert.equal(rows[0].queued, true);
    await queueSelected(rows, async (stream) => sent.push(stream.stream_token), () => {});
    assert.deepEqual(sent, ['0', '3', '5', '6', '4'], 'Retry only failed rows');
    console.log('Season sources: bounded loading, explicit choices, skipped episodes, partial failure and retry passed.');
})().catch(err => { console.error(err); process.exitCode = 1; });
