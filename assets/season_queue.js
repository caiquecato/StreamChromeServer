(function (root) {
    'use strict';
    async function loadSources(rows, fetchSources, changed, active) {
        let cursor = 0;
        async function worker() {
            while (cursor < rows.length && active()) {
                const row = rows[cursor++];
                row.loading = true;
                changed(row);
                try {
                    row.streams = (await fetchSources(row.ep.id)).filter(s => s.playable && s.stream_token);
                    row.choice = '';
                    row.error = '';
                } catch (err) { row.error = err.message; row.streams = []; }
                row.loading = false;
                if (active()) changed(row);
            }
        }
        await Promise.all(Array.from({length: Math.min(3, rows.length)}, worker));
    }
    async function queueSelected(rows, enqueue, changed) {
        for (const row of rows) {
            if (!row.selected || row.queued || !row.choice) continue;
            const stream = row.streams.find(s => s.stream_token === row.choice);
            if (!stream) continue;
            try {
                await enqueue(stream, row.ep);
                row.queued = true;
                row.queueError = '';
            } catch (err) { row.queueError = err.message; }
            changed(row);
        }
    }
    function refreshedChoice(chosen, sources) {
        const match = sources.find(source => source.playable && source.source_key && source.source_key === chosen.source_key);
        if (!match) throw new Error('Selected source is no longer available. Choose a source again.');
        return match;
    }
    const api = {loadSources, queueSelected, refreshedChoice};
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.SeasonQueue = api;
})(typeof window !== 'undefined' ? window : globalThis);
