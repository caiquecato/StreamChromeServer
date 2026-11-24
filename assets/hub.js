(function (root) {
    'use strict';
    const $ = id => document.getElementById(id);
    const t = key => (root.AppUI ? root.AppUI.translate(key) : key);

    let catalogData = { movies: [], series: [], continue_watching: [], featured: null, stats: {} };
    let discoverState = { kind: 'movie', catalog: 'top', items: [], loading: false };
    let localImdbSet = new Set();
    let currentView = 'catalog';
    let currentCatalogItemId = '';
    let currentNextEpisodeId = '';
    let currentAddonNextContext = null;
    let pendingResumeSeconds = 0;
    let progressTimer = null;
    let searchTimer = null;
    let currentImdbMeta = null;
    let downloadJobs = [];
    let downloadPollTimer = null;
    let lastCompletedSet = new Set();
    let activeModalType = '';

    function el(tag, attrs = {}, children = []) {
        const node = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs)) {
            if (k === 'className') node.className = v;
            else if (k === 'text') node.textContent = v;
            else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
            else if (v !== undefined && v !== null) node.setAttribute(k, String(v));
        }
        for (const ch of children) {
            if (ch !== null && ch !== undefined) node.append(typeof ch === 'string' ? document.createTextNode(ch) : ch);
        }
        return node;
    }

    function dotsIconSvg() {
        const ns = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('width', '16');
        svg.setAttribute('height', '16');
        svg.setAttribute('viewBox', '0 0 24 24');
        svg.setAttribute('fill', 'currentColor');
        svg.setAttribute('aria-hidden', 'true');
        for (const cx of [5, 12, 19]) {
            const c = document.createElementNS(ns, 'circle');
            c.setAttribute('cx', String(cx));
            c.setAttribute('cy', '12');
            c.setAttribute('r', '2');
            svg.append(c);
        }
        return svg;
    }

    async function callApi(path, body) {
        if (typeof root.api === 'function') return root.api(path, body);
        const res = await fetch(path, body === undefined ? {} : {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-StreamChrome': '1' }, body: JSON.stringify(body),
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Request failed.');
        return data;
    }

    function fmtDuration(sec) {
        if (!sec || sec <= 0) return '';
        const mins = Math.round(sec / 60);
        if (mins < 60) return `${mins} min`;
        return `${Math.floor(mins / 60)}h ${String(mins % 60).padStart(2, '0')}m`;
    }

    function fmtBytes(bytes) {
        const n = Number(bytes || 0);
        if (!n || n <= 0) return '0 MB';
        const gb = n / (1024 ** 3);
        if (gb >= 1) return `${gb.toFixed(2)} GB`;
        return `${Math.max(0.1, n / (1024 ** 2)).toFixed(1)} MB`;
    }

    function fmtEta(sec) {
        if (sec === null || sec === undefined || !Number.isFinite(Number(sec))) return '';
        const s = Math.max(0, Math.round(Number(sec)));
        const m = Math.floor(s / 60);
        const r = s % 60;
        if (m >= 60) return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, '0')}m`;
        if (m > 0) return `${m}m ${String(r).padStart(2, '0')}s`;
        return `${r}s`;
    }

    function setHubView(view) {
        currentView = view;
        document.body.dataset.view = view;
        if ($('catalogView')) $('catalogView').hidden = view !== 'catalog';
        if ($('discoverView')) $('discoverView').hidden = view !== 'discover';
        for (const [id, key] of [['navCatalog', 'catalog'], ['navDiscover', 'discover'], ['navPlayer', 'player']]) {
            const btn = $(id);
            if (btn) {
                if (key === view) btn.setAttribute('aria-current', 'page');
                else btn.removeAttribute('aria-current');
            }
        }
        if (view === 'catalog') loadCatalog();
        else if (view === 'discover' && !discoverState.items.length) loadDiscover();
    }
    root.setHubView = setHubView;

    function makeMediaCard(item, { wide = false, isSeries = false, isDiscover = false, onClick, onDetails } = {}) {
        const btn = el('button', { type: 'button', className: 'media-card' + (wide ? ' wide-card' : ''), onClick });
        const thumb = el('div', { className: 'media-thumb' });
        const imgUrl = wide ? (item.backdrop_url || item.poster_url) : (item.poster_url || item.poster || item.backdrop_url);
        if (imgUrl) {
            const img = el('img', { src: imgUrl, alt: item.title || '', loading: 'lazy' });
            img.addEventListener('error', () => {
                img.remove();
                thumb.append(el('span', { className: 'media-fallback-title', text: item.title || item.filename || '' }));
            });
            thumb.append(img);
        } else {
            thumb.append(el('span', { className: 'media-fallback-title', text: item.title || item.filename || '' }));
        }
        if (item.rating || item.imdb_rating) {
            const score = item.rating || item.imdb_rating;
            thumb.append(el('span', { className: 'media-badge', text: `IMDb ${score}` }));
        }
        if (isDiscover && item.imdb_id && localImdbSet.has(item.imdb_id)) {
            thumb.append(el('span', { className: 'media-badge media-badge-local', text: t('On Disk') }));
        }
        if (item.progress > 0.01 && item.progress < 0.96) {
            const bar = el('div', { className: 'media-progress' }, [
                el('span', { style: `width:${Math.round(item.progress * 100)}%` }),
            ]);
            thumb.append(bar);
        }
        const subParts = [];
        if (item.year) subParts.push(String(item.year));
        if (isSeries) {
            subParts.push(`${item.season_count || 1} ${t('Season(s)')} · ${item.episode_count || 0} ep.`);
        } else if (item.kind === 'episode') {
            subParts.push(`S${String(item.season || 1).padStart(2, '0')}E${String(item.episode || 1).padStart(2, '0')}`);
        } else if (item.height) {
            subParts.push(`${item.height}p`);
        }
        if (item.duration) subParts.push(fmtDuration(item.duration));
        btn.append(
            thumb,
            el('span', { className: 'media-card-title', text: item.series_title && wide ? `${item.series_title} · ${item.title}` : (item.title || item.filename || '') }),
            el('span', { className: 'media-card-sub', text: subParts.join(' · ') }),
        );

        if (!isDiscover && onDetails) {
            const moreBtn = el('button', {
                type: 'button',
                className: 'media-more-btn',
                title: t('Details'),
                'aria-label': t('Details'),
                onClick: e => {
                    e.stopPropagation();
                    onDetails();
                },
            }, [dotsIconSvg()]);
            return el('div', { className: 'media-card-wrap' }, [btn, moreBtn]);
        }
        return btn;
    }

    function renderCatalog() {
        const container = $('catalogView');
        if (!container) return;
        const term = ($('hubSearchInput')?.value || '').trim().toLowerCase();
        const filter = item => !term || `${item.title || ''} ${item.series_title || ''} ${item.filename || ''} ${item.imdb_id || ''}`.toLowerCase().includes(term);

        const movies = (catalogData.movies || []).filter(filter);
        const series = (catalogData.series || []).filter(filter);
        const cont = (catalogData.continue_watching || []).filter(filter);
        const children = [];

        if (!catalogData.movies?.length && !catalogData.series?.length) {
            children.push(el('div', { className: 'hub-empty-card' }, [
                el('h2', { text: t('Your D:\\StreamServer library is ready') }),
                el('p', { text: t('Place MP4 or MKV movies and series into the folders below, download links directly into D:, or explore IMDb and Stremio titles.') }),
                el('div', { className: 'hub-empty-paths' }, [
                    el('span', { text: 'D:\\StreamServer\\Filmes' }),
                    el('span', { text: 'D:\\StreamServer\\Series' }),
                ]),
                el('div', { className: 'detail-actions' }, [
                    el('button', { type: 'button', className: 'btn btn-play', text: t('Explore Discover'), onClick: () => setHubView('discover') }),
                    el('button', { type: 'button', className: 'btn btn-vlc', text: t('Scan library'), onClick: () => rescanLibrary() }),
                ]),
            ]));
            container.replaceChildren(...children);
            return;
        }

        if (!term && catalogData.featured) {
            const f = catalogData.featured;
            const metaParts = [];
            if (f.year) metaParts.push(String(f.year));
            if (f.rating) metaParts.push(`IMDb ${f.rating}`);
            if (f.height) metaParts.push(`${f.height}p`);
            if (f.video_codec) metaParts.push(String(f.video_codec).toUpperCase());
            if (f.duration) metaParts.push(fmtDuration(f.duration));

            const info = el('div', { className: 'hub-featured-info' }, [
                el('h1', { text: f.series_title ? `${f.series_title}` : (f.title || '') }),
                el('div', { className: 'hub-meta-line', text: metaParts.join(' · ') }),
                f.overview ? el('p', { className: 'hub-overview', text: f.overview }) : null,
                el('div', { className: 'hub-featured-actions' }, [
                    el('button', {
                        type: 'button', className: 'btn btn-play',
                        text: f.position > 10 ? t('Resume playback') : t('Watch now'),
                        onClick: () => playCatalogItem(f.id, f.position || 0),
                    }),
                    f.series_key ? el('button', {
                        type: 'button', className: 'btn btn-vlc', text: t('Episodes'),
                        onClick: () => openSeriesModal(f.series_key),
                    }) : null,
                    el('button', {
                        type: 'button', className: 'btn btn-vlc', text: t('Details'),
                        onClick: () => openLocalDetailModal(f),
                    }),
                ]),
            ]);
            const media = el('div', { className: 'hub-featured-media' });
            const bg = f.backdrop_url || f.poster_url;
            if (bg) media.append(el('img', { src: bg, alt: f.title || '' }));
            children.push(el('section', { className: 'hub-featured' }, [info, media]));
        }

        if (cont.length) {
            children.push(el('section', { className: 'hub-section' }, [
                el('div', { className: 'hub-section-head' }, [el('h2', { text: t('Continue watching') })]),
                el('div', { className: 'wide-grid' }, cont.map(item => makeMediaCard(item, {
                    wide: true,
                    onClick: () => playCatalogItem(item.id, item.position || 0),
                    onDetails: () => openLocalDetailModal(item),
                }))),
            ]));
        }

        if (movies.length) {
            children.push(el('section', { className: 'hub-section' }, [
                el('div', { className: 'hub-section-head' }, [
                    el('h2', { text: `${t('Movies')} (${movies.length})` }),
                    el('button', { type: 'button', className: 'btn btn-vlc', text: t('Scan library'), onClick: () => rescanLibrary() }),
                ]),
                el('div', { className: 'poster-grid' }, movies.map(item => makeMediaCard(item, {
                    onClick: () => playCatalogItem(item.id, item.position || 0),
                    onDetails: () => openLocalDetailModal(item),
                }))),
            ]));
        }

        if (series.length) {
            children.push(el('section', { className: 'hub-section' }, [
                el('div', { className: 'hub-section-head' }, [
                    el('h2', { text: `${t('TV Shows')} (${series.length})` }),
                    !movies.length ? el('button', { type: 'button', className: 'btn btn-vlc', text: t('Scan library'), onClick: () => rescanLibrary() }) : null,
                ]),
                el('div', { className: 'poster-grid' }, series.map(show => makeMediaCard(show, {
                    isSeries: true,
                    onClick: () => openSeriesModal(show.series_key),
                    onDetails: () => openLocalDetailModal({ ...show, kind: 'series' }),
                }))),
            ]));
        }

        if (!movies.length && !series.length && term) {
            children.push(el('div', { className: 'hub-empty-card' }, [
                el('h2', { text: t('No local matches') }),
                el('p', { text: t('Search this title on Discover instead.') }),
                el('div', { className: 'detail-actions' }, [
                    el('button', { type: 'button', className: 'btn btn-play', text: t('Search in Discover'), onClick: () => { setHubView('discover'); loadDiscover(); } }),
                ]),
            ]));
        }

        container.replaceChildren(...children);
    }

    async function loadCatalog() {
        try {
            const data = await callApi('/api/catalog');
            catalogData = data;
            localImdbSet = new Set([
                ...(data.movies || []).map(m => m.imdb_id).filter(Boolean),
                ...(data.series || []).map(s => s.imdb_id).filter(Boolean),
            ]);
            renderCatalog();
        } catch {
            // Keep view quiet if offline during transient restart.
        }
    }
    root.loadCatalog = loadCatalog;

    async function rescanLibrary() {
        try {
            const data = await callApi('/api/catalog/scan', {});
            catalogData = data;
            renderCatalog();
        } catch {}
    }

    function renderDiscover() {
        const container = $('discoverView');
        if (!container) return;
        const pills = el('div', { className: 'hub-pills' }, [
            ['movie', 'top', 'Trending Movies'],
            ['series', 'top', 'Trending Series'],
            ['movie', 'imdbRating', 'Top IMDb Movies'],
            ['series', 'imdbRating', 'Top IMDb Series'],
        ].map(([kind, cat, label]) => el('button', {
            type: 'button',
            text: t(label),
            'aria-pressed': String(discoverState.kind === kind && discoverState.catalog === cat),
            onClick: () => {
                discoverState.kind = kind;
                discoverState.catalog = cat;
                loadDiscover();
            },
        })));

        const head = el('div', { className: 'hub-section-head' }, [
            pills,
            el('button', { type: 'button', className: 'btn btn-vlc', text: t('Addons'), onClick: () => openAddonsModal() }),
        ]);

        let body;
        if (discoverState.loading) {
            body = el('p', { className: 'notice', text: t('Loading IMDb & Stremio catalog…') });
        } else if (!discoverState.items.length) {
            body = el('p', { className: 'notice', text: t('No titles found. Try another title or IMDb ID (tt...).') });
        } else {
            body = el('div', { className: 'poster-grid' }, discoverState.items.map(item => makeMediaCard(item, {
                isDiscover: true,
                onClick: () => openImdbModal(item.kind, item.imdb_id),
            })));
        }
        container.replaceChildren(head, body);
    }

    async function loadDiscover() {
        discoverState.loading = true;
        renderDiscover();
        const q = ($('hubSearchInput')?.value || '').trim();
        try {
            const params = new URLSearchParams({ kind: discoverState.kind, catalog: discoverState.catalog });
            if (q) params.set('q', q);
            const data = await callApi('/api/discover?' + params);
            discoverState.items = data.items || [];
        } catch {
            discoverState.items = [];
        } finally {
            discoverState.loading = false;
            renderDiscover();
        }
    }

    function openModal(titleText, bodyNodes, type = '') {
        const backdrop = $('hubModalBackdrop');
        if (!backdrop) return;
        activeModalType = type;
        $('hubModalTitle').textContent = titleText;
        $('hubModalBody').replaceChildren(...bodyNodes);
        backdrop.hidden = false;
    }

    function closeModal() {
        const backdrop = $('hubModalBackdrop');
        if (backdrop) backdrop.hidden = true;
        activeModalType = '';
    }
    root.closeHubModal = closeModal;

    async function openLocalDetailModal(item) {
        openModal(item.title || t('Details'), [el('p', { className: 'notice', text: t('Loading details…') })], 'details');
        try {
            const isSeries = item.kind === 'series' || (!item.id && item.series_key);
            const qs = isSeries
                ? `series_key=${encodeURIComponent(item.series_key)}`
                : `id=${encodeURIComponent(item.id)}`;
            const d = await callApi('/api/catalog/details?' + qs);

            const ratingPills = [];
            if (d.imdb_rating) ratingPills.push(el('span', { className: 'rating-pill' }, [el('span', { text: 'IMDb' }), `${d.imdb_rating} / 10`]));
            if (d.rt_critics) ratingPills.push(el('span', { className: 'rating-pill' }, [el('span', { text: t('Rotten Tomatoes (Critics)') }), d.rt_critics]));
            if (d.rt_audience) ratingPills.push(el('span', { className: 'rating-pill' }, [el('span', { text: t('Rotten Tomatoes (Audience)') }), d.rt_audience]));

            const primaryBtn = isSeries
                ? el('button', { type: 'button', className: 'btn btn-play', text: t('Episodes'), onClick: () => openSeriesModal(d.series_key) })
                : el('button', { type: 'button', className: 'btn btn-play', text: t('Watch now'), onClick: () => { closeModal(); playCatalogItem(d.id, item.position || 0); } });

            const actions = [primaryBtn];
            if (d.imdb_id) {
                actions.push(el('button', {
                    type: 'button', className: 'btn btn-vlc', text: t('View in Discover'),
                    onClick: () => openImdbModal(isSeries ? 'series' : 'movie', d.imdb_id),
                }));
            }

            const hero = el('div', { className: 'detail-hero' }, [
                d.poster_url ? el('img', { className: 'detail-poster', src: d.poster_url, alt: '' }) : el('div', { className: 'detail-poster' }),
                el('div', { className: 'detail-info' }, [
                    el('div', { className: 'hub-meta-line', text: [d.year, d.genres, isSeries ? `${d.season_count} ${t('Season(s)')} · ${d.episode_count} ep.` : fmtDuration(d.duration)].filter(Boolean).join(' · ') }),
                    ratingPills.length ? el('div', { className: 'rating-pills' }, ratingPills) : null,
                    d.overview ? el('p', { className: 'notice', text: d.overview }) : null,
                    d.director ? el('div', { className: 'notice', text: `${t('Director')}: ${d.director}` }) : null,
                    d.cast_text ? el('div', { className: 'notice', text: `${t('Cast')}: ${d.cast_text}` }) : null,
                    el('div', { className: 'detail-actions' }, actions),
                ]),
            ]);

            const specs = [
                [t('Resolution'), d.resolution_label || (d.height ? `${d.height}p` : '—')],
                [t('Bitrate'), d.bitrate_label || '—'],
                [t('Duration'), fmtDuration(d.duration) || '—'],
                [t('Video Codec'), [d.video_profile || String(d.video_codec || '').toUpperCase(), d.fps].filter(Boolean).join(' · ') || '—'],
                [t('Audio Tracks'), d.audio_summary || '—'],
                [t('Size'), d.size_label || '—'],
                d.subtitle_summary || d.sidecar_count
                    ? [t('Subtitles'), [d.subtitle_summary, d.sidecar_count ? `${d.sidecar_count} ${t('local sub(s)')}` : ''].filter(Boolean).join(' · ')]
                    : null,
            ].filter(Boolean);

            const specsGrid = el('div', { className: 'specs-grid' }, specs.map(([label, val]) => el('div', { className: 'spec-box' }, [
                el('span', { className: 'spec-label', text: label }),
                el('span', { className: 'spec-value', text: val }),
            ])));

            openModal(`${d.title}${d.year ? ` (${d.year})` : ''}`, [hero, el('h3', { text: t('Technical Specifications') }), specsGrid], 'details');
        } catch (err) {
            openModal(item.title || t('Details'), [el('p', { className: 'notice', 'data-error': 'true', text: err.message })], 'details');
        }
    }

    function updateDownloadButtonsInDom() {
        const byToken = new Map();
        for (const job of downloadJobs) {
            if (job.stream_token) byToken.set(job.stream_token, job);
        }
        for (const btn of document.querySelectorAll('[data-dl-token]')) {
            const job = byToken.get(btn.dataset.dlToken);
            if (!job) continue;
            if (job.state === 'queued') {
                btn.textContent = t('Queued');
                btn.disabled = true;
            } else if (job.state === 'downloading') {
                const pct = job.total > 0 ? Math.min(99, Math.round((job.downloaded / job.total) * 100)) : 0;
                btn.textContent = `${t('Downloading')} ${pct}%`;
                btn.disabled = true;
            } else if (job.state === 'complete') {
                btn.textContent = t('Downloaded');
                btn.disabled = true;
            } else if (job.state === 'failed') {
                btn.textContent = t('Download to D:');
                btn.disabled = false;
            }
        }
    }

    function updateDownloadsBadge() {
        const badge = $('downloadsBadge');
        if (!badge) return;
        const active = downloadJobs.filter(j => j.state === 'queued' || j.state === 'downloading');
        if (!active.length) {
            badge.hidden = true;
            badge.textContent = '0';
            return;
        }
        const totalBytes = active.reduce((acc, j) => acc + (j.total || 0), 0);
        const dlBytes = active.reduce((acc, j) => acc + (j.downloaded || 0), 0);
        const pct = totalBytes > 0 ? Math.round((dlBytes / totalBytes) * 100) : 0;
        badge.textContent = pct > 0 ? `${active.length} · ${pct}%` : String(active.length);
        badge.hidden = false;
    }

    function ensureDownloadPolling() {
        const hasActive = downloadJobs.some(j => j.state === 'queued' || j.state === 'downloading');
        if (hasActive && !downloadPollTimer) {
            downloadPollTimer = setInterval(refreshLibraryDownloads, 1500);
        } else if (!hasActive && downloadPollTimer) {
            clearInterval(downloadPollTimer);
            downloadPollTimer = null;
        }
    }

    async function refreshLibraryDownloads() {
        try {
            const data = await callApi('/api/library/downloads');
            downloadJobs = data.jobs || [];
            updateDownloadsBadge();
            updateDownloadButtonsInDom();

            let newCompletion = false;
            for (const j of downloadJobs) {
                if (j.state === 'complete' && !lastCompletedSet.has(j.id)) {
                    lastCompletedSet.add(j.id);
                    newCompletion = true;
                }
            }
            if (newCompletion) loadCatalog();
            if (activeModalType === 'downloads' && !$('hubModalBackdrop')?.hidden) {
                renderDownloadsModalBody();
            }
            ensureDownloadPolling();
        } catch {
            ensureDownloadPolling();
        }
    }

    function renderDownloadsModalBody() {
        const body = $('hubModalBody');
        if (!body) return;
        if (!downloadJobs.length) {
            body.replaceChildren(el('p', {
                className: 'notice',
                text: t('No downloads in this session yet. Open any title in IMDb & Stremio and click Download to D:.'),
            }));
            return;
        }

        const hasFinished = downloadJobs.some(j => !['queued', 'downloading'].includes(j.state));
        const headerBar = el('div', { className: 'hub-section-head' }, [
            el('span', { className: 'notice', text: t('Saved to D: and indexed in Catalog + Jellyfin.') }),
            hasFinished ? el('button', {
                type: 'button', className: 'btn btn-vlc', text: t('Clear finished'),
                onClick: async () => {
                    await callApi('/api/library/download/clear', {});
                    await refreshLibraryDownloads();
                },
            }) : null,
        ]);

        const list = el('div', { className: 'dl-list' }, downloadJobs.map(job => {
            const pct = job.state === 'complete' ? 100 : (job.total > 0 ? Math.min(99, Math.round((job.downloaded / job.total) * 100)) : 0);
            const stateLabel = job.state === 'queued' ? t('Queued')
                : job.state === 'downloading' ? t('Downloading')
                : job.state === 'complete' ? t('Downloaded')
                : job.state === 'cancelled' ? t('Cancelled')
                : t('Failed');
            const speedMb = job.speed > 0 ? `${(job.speed / (1024 * 1024)).toFixed(1)} MB/s` : '';
            const etaText = job.state === 'downloading' && job.eta ? `ETA ${fmtEta(job.eta)}` : '';
            const sizeText = job.total > 0 ? `${fmtBytes(job.downloaded)} / ${fmtBytes(job.total)}` : fmtBytes(job.downloaded);

            const actionBtn = (job.state === 'queued' || job.state === 'downloading')
                ? el('button', {
                    type: 'button', className: 'btn btn-vlc', text: t('Cancel'),
                    onClick: async () => {
                        await callApi('/api/library/download/cancel', { id: job.id });
                        refreshLibraryDownloads();
                    },
                })
                : (job.state === 'complete' && job.catalog_item_id)
                    ? el('button', {
                        type: 'button', className: 'btn btn-play', text: t('Watch downloaded'),
                        onClick: () => { closeModal(); playCatalogItem(job.catalog_item_id, 0); },
                    })
                    : null;

            return el('div', { className: 'dl-job-card' }, [
                el('div', { className: 'dl-job-top' }, [
                    el('div', {}, [
                        el('div', { className: 'episode-row-title', text: `${job.title}${job.subtitle ? ` · ${job.subtitle}` : ''}` }),
                        el('div', { className: 'stream-filename', text: `D:\\StreamServer\\${job.rel_path.replace(/\//g, '\\')}` }),
                    ]),
                    actionBtn || el('span', { className: 'stream-tag', text: stateLabel }),
                ]),
                el('div', { className: 'dl-progress-bar' }, [
                    el('div', { className: 'dl-progress-fill', style: `width:${pct}%` }),
                ]),
                el('div', { className: 'dl-job-meta' }, [
                    el('span', { text: `${stateLabel} · ${pct}%` }),
                    el('span', { text: [sizeText, speedMb, etaText, job.error].filter(Boolean).join(' · ') }),
                ]),
            ]);
        }));

        body.replaceChildren(headerBar, list);
    }

    async function openDownloadsModal() {
        openModal(t('Library Downloads (D:\\StreamServer)'), [el('p', { className: 'notice', text: t('Loading downloads…') })], 'downloads');
        await refreshLibraryDownloads();
        if (activeModalType === 'downloads') renderDownloadsModalBody();
    }
    root.openDownloadsModal = openDownloadsModal;

    async function enqueueStreamDownload(st, meta, epObj, btn) {
        if (!st?.stream_token) return;
        btn.disabled = true;
        btn.textContent = t('Queued');
        try {
            await callApi('/api/library/download', {
                stream_token: st.stream_token,
                kind: meta.kind,
                title: meta.title,
                year: meta.year,
                season: epObj?.season,
                episode: epObj?.episode,
                episode_title: epObj?.title,
                filename: st.filename,
                imdb_id: meta.imdb_id,
            });
            await refreshLibraryDownloads();
        } catch (err) {
            btn.disabled = false;
            btn.textContent = t('Download to D:');
            if ($('playerStatus')) root.AppUI?.text($('playerStatus'), err.message);
        }
    }

    function findNextEpisodeObj(meta, currentEp) {
        if (!meta?.seasons?.length || !currentEp) return null;
        const flat = [];
        for (const s of meta.seasons) {
            for (const ep of (s.episodes || [])) flat.push(ep);
        }
        const idx = flat.findIndex(e => e.id === currentEp.id || (Number(e.season) === Number(currentEp.season) && Number(e.episode) === Number(currentEp.episode)));
        return idx >= 0 && idx + 1 < flat.length ? flat[idx + 1] : null;
    }

    async function playAddonStream(st, meta, epObj = null) {
        if (!st?.stream_token) return;
        currentCatalogItemId = '';
        currentNextEpisodeId = '';
        const nextEp = (meta?.kind === 'series' && epObj) ? findNextEpisodeObj(meta, epObj) : null;
        currentAddonNextContext = nextEp ? { meta, nextEp, bingeGroup: st.binge_group || '', preferPt: Boolean(st.pt_audio) } : null;
        if ($('nextEpisodeButton')) $('nextEpisodeButton').hidden = !currentAddonNextContext;

        closeModal();
        setHubView('player');
        const label = epObj ? `${meta.title} S${String(epObj.season).padStart(2, '0')}E${String(epObj.episode).padStart(2, '0')}` : (st.filename || meta.title || 'Stream');
        await root.loadDownloadedVideo?.(st.stream_token, label, 0);

        const sel = $('selectSub');
        if (sel && sel.options.length > 1 && !sel.value) {
            const ptOpt = Array.from(sel.options).find(o => o.value && /portug|pt-br|pob/i.test(o.textContent || ''));
            if (ptOpt) {
                sel.value = ptOpt.value;
                await root.onSubtitleChange?.();
            }
        }
    }

    async function openSeriesModal(seriesKey) {
        openModal(t('Loading…'), [el('p', { className: 'notice', text: t('Loading episodes…') })], 'series');
        try {
            const detail = await callApi('/api/catalog/series?key=' + encodeURIComponent(seriesKey));
            const seasons = detail.seasons || [];
            let activeSeason = seasons[0]?.season || 1;
            const epList = el('div', { className: 'episode-list' });
            const pills = el('div', { className: 'hub-pills' });

            const drawEpisodes = () => {
                const seasonObj = seasons.find(s => s.season === activeSeason) || seasons[0];
                epList.replaceChildren(...(seasonObj?.episodes || []).map(ep => {
                    const row = el('button', { type: 'button', className: 'episode-row', onClick: () => { closeModal(); playCatalogItem(ep.id, ep.position || 0); } });
                    const thumb = ep.backdrop_url ? el('img', { className: 'episode-thumb', src: ep.backdrop_url, alt: '' }) : el('div', { className: 'episode-thumb' });
                    const text = el('div', {}, [
                        el('div', { className: 'episode-row-title', text: `E${String(ep.episode || 1).padStart(2, '0')} · ${ep.title || ep.filename}` }),
                        el('div', { className: 'episode-row-meta', text: [fmtDuration(ep.duration), ep.subtitles?.length ? `${ep.subtitles.length} ${t('local sub(s)')}` : ''].filter(Boolean).join(' · ') }),
                    ]);
                    row.append(thumb, text, el('span', { className: 'btn btn-play', text: ep.position > 10 ? t('Resume') : t('Play') }));
                    return row;
                }));
                for (const b of pills.querySelectorAll('button')) {
                    b.setAttribute('aria-pressed', String(Number(b.dataset.season) === activeSeason));
                }
            };

            pills.replaceChildren(...seasons.map(s => el('button', {
                type: 'button', 'data-season': s.season, text: `${t('Season')} ${s.season}`,
                onClick: () => { activeSeason = s.season; drawEpisodes(); },
            })));
            drawEpisodes();

            const hero = el('div', { className: 'detail-hero' }, [
                detail.poster_url ? el('img', { className: 'detail-poster', src: detail.poster_url, alt: '' }) : el('div', { className: 'detail-poster' }),
                el('div', { className: 'detail-info' }, [
                    el('div', { className: 'hub-meta-line', text: [detail.year, detail.rating ? `IMDb ${detail.rating}` : '', detail.cast_text].filter(Boolean).join(' · ') }),
                    detail.overview ? el('p', { className: 'notice', text: detail.overview }) : null,
                    el('div', { className: 'detail-actions' }, [
                        el('button', { type: 'button', className: 'btn btn-vlc', text: t('Details'), onClick: () => openLocalDetailModal({ series_key: detail.series_key, kind: 'series', title: detail.title }) }),
                        detail.imdb_id ? el('button', { type: 'button', className: 'btn btn-vlc', text: t('View in Discover'), onClick: () => openImdbModal('series', detail.imdb_id) }) : null,
                    ]),
                ]),
            ]);
            openModal(detail.title || t('TV Shows'), [hero, pills, epList], 'series');
        } catch (err) {
            openModal(t('TV Shows'), [el('p', { className: 'notice', 'data-error': 'true', text: err.message })], 'series');
        }
    }

    async function openImdbModal(kind, imdbId, initialVideoId = '') {
        openModal(t('Discover'), [el('p', { className: 'notice', text: t('Loading IMDb & Stremio metadata…') })], 'imdb');
        try {
            const params = new URLSearchParams({ kind, id: imdbId, streams: '0' });
            const meta = await callApi('/api/imdb/meta?' + params);
            currentImdbMeta = meta;

            const actions = [];
            if (meta.local_matches?.length) {
                const firstLocal = meta.local_matches[0];
                actions.push(el('button', {
                    type: 'button', className: 'btn btn-play',
                    text: t('Watch from Disk (D:)'),
                    onClick: () => { closeModal(); playCatalogItem(firstLocal.id, 0); },
                }));
            }
            actions.push(el('a', { className: 'btn btn-vlc', href: meta.stremio_url, text: t('Open in Stremio') }));
            actions.push(el('button', {
                type: 'button', className: 'btn btn-vlc', text: t('Search Subtitles'),
                onClick: () => {
                    closeModal();
                    setHubView('player');
                    if (typeof root.setSidePanel === 'function') root.setSidePanel('subtitle');
                    if ($('searchTitle')) $('searchTitle').value = meta.imdb_id;
                    if ($('searchType')) { $('searchType').value = kind === 'series' ? 'series' : 'movie'; root.updateEpisodeFields?.(); }
                    root.searchSubtitles?.();
                },
            }));
            actions.push(el('a', { className: 'btn btn-vlc', href: meta.imdb_url, target: '_blank', rel: 'noopener noreferrer', text: 'IMDb' }));

            const hero = el('div', { className: 'detail-hero' }, [
                meta.poster ? el('img', { className: 'detail-poster', src: meta.poster, alt: '' }) : el('div', { className: 'detail-poster' }),
                el('div', { className: 'detail-info' }, [
                    el('div', { className: 'hub-meta-line', text: [meta.year, meta.runtime, meta.imdb_rating ? `IMDb ${meta.imdb_rating}` : '', (meta.genres || []).join(', ')].filter(Boolean).join(' · ') }),
                    meta.cast?.length ? el('div', { className: 'notice', text: `${t('Cast')}: ${meta.cast.join(', ')}` }) : null,
                    meta.description ? el('p', { className: 'notice', text: meta.description }) : null,
                    el('div', { className: 'detail-actions' }, actions),
                ]),
            ]);

            const streamTitle = el('h3', { text: t('Stremio Addon Streams') });
            const streamFilters = el('div', { className: 'hub-pills', hidden: 'hidden' });
            const streamHead = el('div', { className: 'stream-section-head' }, [streamTitle, streamFilters]);
            const streamList = el('div', { className: 'stream-list' });
            let loadedStreams = [];
            let activeFilter = 'all';
            let activeEp = null;
            let streamFetchGen = 0;

            const drawStreams = () => {
                if (!loadedStreams.length) {
                    streamFilters.hidden = true;
                    streamList.replaceChildren(el('p', { className: 'notice', text: t('No playable HTTP/Debrid streams returned for this title.') }));
                    return;
                }
                const counts = {
                    all: loadedStreams.length,
                    '4K': loadedStreams.filter(s => s.resolution === '4K').length,
                    '1080p': loadedStreams.filter(s => s.resolution === '1080p').length,
                    pt: loadedStreams.filter(s => s.pt_audio).length,
                };
                if (!counts[activeFilter] && activeFilter !== 'all') activeFilter = 'all';
                streamFilters.hidden = false;
                streamFilters.replaceChildren(
                    ...[
                        ['all', `${t('All sources')} (${counts.all})`],
                        counts['4K'] ? ['4K', `4K (${counts['4K']})`] : null,
                        counts['1080p'] ? ['1080p', `1080p (${counts['1080p']})`] : null,
                        counts.pt ? ['pt', `PT / Dual (${counts.pt})`] : null,
                    ].filter(Boolean).map(([fKey, label]) => el('button', {
                        type: 'button', text: label, 'aria-pressed': String(activeFilter === fKey),
                        onClick: () => { activeFilter = fKey; drawStreams(); },
                    })),
                );

                const visible = loadedStreams.filter(st => {
                    if (activeFilter === '4K') return st.resolution === '4K';
                    if (activeFilter === '1080p') return st.resolution === '1080p';
                    if (activeFilter === 'pt') return st.pt_audio;
                    return true;
                });

                streamList.replaceChildren(...visible.map(st => {
                    const badges = [
                        st.resolution ? el('span', { className: st.resolution === '4K' ? 'stream-tag stream-tag-accent' : 'stream-tag', text: st.resolution }) : null,
                        st.cached ? el('span', { className: 'stream-tag', text: 'TB Instant' }) : null,
                        st.pt_audio ? el('span', { className: 'stream-tag stream-tag-accent', text: 'PT / Dual' }) : null,
                        st.size_label ? el('span', { className: 'stream-tag', text: st.size_label }) : null,
                        el('span', { className: 'stream-tag', text: st.addon || 'Addon' }),
                    ].filter(Boolean);

                    const watchBtn = st.playable
                        ? el('button', {
                            type: 'button', className: 'btn btn-play', text: t('Watch now'),
                            onClick: () => playAddonStream(st, meta, activeEp),
                        })
                        : el('a', { className: 'btn btn-vlc', href: st.stremio_url, text: t('Open in Stremio') });

                    const dlBtn = st.playable
                        ? el('button', {
                            type: 'button', className: 'btn btn-vlc', 'data-dl-token': st.stream_token, text: t('Download to D:'),
                            onClick: e => enqueueStreamDownload(st, meta, activeEp, e.currentTarget),
                        })
                        : null;

                    return el('div', { className: 'stream-card' }, [
                        el('div', { className: 'stream-card-main' }, [
                            el('div', { className: 'stream-badges' }, badges),
                            el('div', { className: 'stream-summary', text: st.summary || st.name }),
                            st.filename ? el('div', { className: 'stream-filename', text: st.filename }) : null,
                        ]),
                        el('div', { className: 'stream-actions' }, [watchBtn, dlBtn].filter(Boolean)),
                    ]);
                }));
                updateDownloadButtonsInDom();
            };

            const loadStreamsFor = async (videoId, epObj = null) => {
                const gen = ++streamFetchGen;
                activeEp = epObj;
                streamTitle.textContent = epObj
                    ? `${t('Stremio Addon Streams')} · S${String(epObj.season).padStart(2, '0')}E${String(epObj.episode).padStart(2, '0')}`
                    : t('Stremio Addon Streams');
                streamFilters.hidden = true;
                streamList.replaceChildren(el('p', { className: 'notice', text: t('Fetching AIOStreams sources…') }));
                try {
                    const sData = await callApi(`/api/stremio/streams?kind=${encodeURIComponent(kind)}&id=${encodeURIComponent(videoId)}`);
                    if (gen !== streamFetchGen) return;
                    loadedStreams = sData.streams || [];
                    drawStreams();
                } catch (err) {
                    if (gen !== streamFetchGen) return;
                    streamList.replaceChildren(el('p', { className: 'notice', 'data-error': 'true', text: err.message }));
                }
            };

            const nodes = [hero];
            if (kind === 'series' && meta.seasons?.length) {
                let targetEp = null;
                if (initialVideoId) {
                    for (const s of meta.seasons) {
                        const hit = (s.episodes || []).find(ep => ep.id === initialVideoId);
                        if (hit) { targetEp = hit; break; }
                    }
                }
                if (!targetEp) targetEp = meta.seasons[0].episodes?.[0] || null;
                let sNum = targetEp ? targetEp.season : meta.seasons[0].season;
                const epWrap = el('div', { className: 'episode-list' });
                const sPills = el('div', { className: 'hub-pills' });
                let selectedEpId = targetEp ? targetEp.id : '';

                const drawS = () => {
                    const sObj = meta.seasons.find(s => s.season === sNum) || meta.seasons[0];
                    epWrap.replaceChildren(...(sObj?.episodes || []).map(ep => {
                        const localMatch = (meta.local_matches || []).find(m => Number(m.season) === Number(ep.season) && Number(m.episode) === Number(ep.episode));
                        const row = el('div', { className: 'addon-row' + (ep.id === selectedEpId ? ' episode-row-active' : '') }, [
                            el('div', {}, [
                                el('div', { className: 'episode-row-title', text: `E${String(ep.episode).padStart(2, '0')} · ${ep.title}` }),
                                el('div', { className: 'episode-row-meta', text: ep.released || '' }),
                            ]),
                            el('div', { className: 'stream-actions' }, [
                                localMatch ? el('button', { type: 'button', className: 'btn btn-play', text: t('Watch from Disk (D:)'), onClick: () => { closeModal(); playCatalogItem(localMatch.id, 0); } }) : null,
                                el('button', {
                                    type: 'button', className: 'btn btn-vlc', text: t('Load streams / subs'),
                                    onClick: () => {
                                        selectedEpId = ep.id;
                                        drawS();
                                        loadStreamsFor(ep.id, ep);
                                    },
                                }),
                            ].filter(Boolean)),
                        ]);
                        return row;
                    }));
                    for (const b of sPills.querySelectorAll('button')) b.setAttribute('aria-pressed', String(Number(b.dataset.season) === sNum));
                };
                sPills.replaceChildren(...meta.seasons.map(s => el('button', {
                    type: 'button', 'data-season': s.season, text: `${t('Season')} ${s.season}`,
                    onClick: () => { sNum = s.season; drawS(); },
                })));
                drawS();
                nodes.push(el('p', { className: 'notice', text: t('Click an episode below to load AIOStreams watch and download sources.') }), sPills, epWrap);
                if (targetEp) setTimeout(() => loadStreamsFor(targetEp.id, targetEp), 0);
            } else {
                setTimeout(() => loadStreamsFor(meta.imdb_id, null), 0);
            }

            nodes.push(streamHead, streamList);
            openModal(`${meta.title} (${meta.imdb_id})`, nodes, 'imdb');
        } catch (err) {
            openModal(t('Discover'), [el('p', { className: 'notice', 'data-error': 'true', text: err.message })], 'imdb');
        }
    }

    async function openAddonsModal() {
        openModal(t('Addons'), [el('p', { className: 'notice', text: t('Loading…') })], 'addons');
        try {
            const data = await callApi('/api/stremio/addons');
            const list = el('div', { className: 'episode-list' });
            const status = el('p', { className: 'notice' });
            const input = el('input', { type: 'url', className: 'url-input', placeholder: 'https://.../manifest.json' });

            const draw = addons => {
                if (!addons.length) {
                    list.replaceChildren(el('p', { className: 'notice', text: t('No Stremio addons configured yet. Paste any addon manifest.json URL above.') }));
                    return;
                }
                list.replaceChildren(...addons.map(a => el('div', { className: 'addon-row' }, [
                    el('div', {}, [
                        el('div', { className: 'episode-row-title', text: a.name }),
                        el('div', { className: 'episode-row-meta', text: a.masked_url || a.url || '' }),
                    ]),
                    el('button', {
                        type: 'button', className: 'btn btn-vlc', text: t('Remove'),
                        onClick: async () => {
                            const res = await callApi('/api/stremio/addons', { action: 'remove', key: a.key || a.url });
                            draw(res.addons || []);
                        },
                    }),
                ])));
            };
            draw(data.addons || []);

            const addForm = el('form', {
                className: 'remote-url-row',
                onSubmit: async e => {
                    e.preventDefault();
                    status.textContent = t('Validating addon manifest…');
                    try {
                        const res = await callApi('/api/stremio/addons', { action: 'add', url: input.value.trim() });
                        input.value = '';
                        status.textContent = t('Addon added.');
                        draw(res.addons || []);
                    } catch (err) {
                        status.textContent = err.message;
                    }
                },
            }, [input, el('button', { type: 'submit', className: 'btn btn-play', text: t('Add addon') })]);

            openModal(t('Addons'), [
                el('p', { className: 'notice', text: t('Configure Stremio addon manifest.json URLs to query HTTP streams directly inside StreamChrome Server.') }),
                addForm,
                status,
                list,
            ], 'addons');
        } catch (err) {
            openModal(t('Addons'), [el('p', { className: 'notice', text: err.message })], 'addons');
        }
    }

    function qrBlock(url) {
        const wrap = el('div', { className: 'qr-wrap' });
        if (root.QrSvg && url) wrap.append(root.QrSvg.renderSvgElement(url, 132));
        const input = el('input', { type: 'text', value: url, readonly: 'readonly' });
        const copyBtn = el('button', {
            type: 'button', className: 'btn btn-vlc', text: t('Copy'),
            onClick: () => {
                navigator.clipboard?.writeText(url);
                copyBtn.textContent = t('Copied');
                setTimeout(() => { copyBtn.textContent = t('Copy'); }, 1500);
            },
        });
        wrap.append(el('div', { className: 'remote-url-row', style: 'flex:1;min-width:200px' }, [input, copyBtn]));
        return wrap;
    }

    async function openRemoteModal() {
        openModal(t('TV & Remote Access'), [el('p', { className: 'notice', text: t('Loading network status…') })], 'remote');
        try {
            const info = await callApi('/api/network/info');

            const lanBox = el('div', { className: 'remote-box' }, [
                el('h3', { text: t('Home Wi-Fi (Smart TV TCL & Phone)') }),
                el('p', { className: 'notice', text: t('Install Jellyfin on your TCL TV (Google Play) or Streamyfin on Android and connect to:') }),
                qrBlock(info.jellyfin_lan_url),
                el('p', { className: 'notice', text: `${t('StreamChrome Web Hub (Browser / PWA)')}: ${info.hub_lan_url}` }),
                info.jellyfin?.jellyfin_password ? el('p', { className: 'notice', text: `Login Jellyfin: ${info.jellyfin.username || 'caique'} / ${info.jellyfin.jellyfin_password}` }) : null,
                el('div', { className: 'detail-actions' }, [
                    el('button', {
                        type: 'button', className: 'btn btn-play', text: t('Open Jellyfin TV catalog'),
                        onClick: () => openJellyfin(),
                    }),
                    el('button', {
                        type: 'button', className: 'btn btn-vlc', text: t('Allow TV in Windows Firewall'),
                        onClick: () => callApi('/api/system/firewall', {}),
                    }),
                    el('button', {
                        type: 'button', className: 'btn btn-vlc', text: t('Copy M3U Playlist'),
                        onClick: () => navigator.clipboard?.writeText(info.m3u_lan_url),
                    }),
                ]),
            ]);

            const statusMsg = el('p', { className: 'notice' });
            const pinInput = el('input', { type: 'password', placeholder: info.remote?.has_pin ? t('PIN configured (enter new to change)') : t('Set 4-16 char PIN') });
            const jfPwInput = el('input', { type: 'password', placeholder: t('New password for Jellyfin user caique') });

            const remoteBox = el('div', { className: 'remote-box' }, [
                el('h3', { text: t('Away from Home (Cloudflare HTTPS, no VPN)') }),
                el('p', { className: 'notice', text: t('Expose Jellyfin or StreamChrome Hub safely over HTTPS without Tailscale or router port forwarding.') }),
                el('div', { className: 'remote-url-row' }, [
                    pinInput,
                    el('button', {
                        type: 'button', className: 'btn btn-vlc', text: t('Save PIN'),
                        onClick: async () => {
                            try {
                                await callApi('/api/remote/pin', { pin: pinInput.value });
                                pinInput.value = '';
                                openRemoteModal();
                            } catch (e) { statusMsg.textContent = e.message; }
                        },
                    }),
                ]),
                el('div', { className: 'remote-url-row' }, [
                    jfPwInput,
                    el('button', {
                        type: 'button', className: 'btn btn-vlc', text: t('Set Jellyfin Pass'),
                        onClick: async () => {
                            try {
                                await callApi('/api/jellyfin/password', { password: jfPwInput.value });
                                jfPwInput.value = '';
                                statusMsg.textContent = t('Jellyfin password updated.');
                            } catch (e) { statusMsg.textContent = e.message; }
                        },
                    }),
                ]),
                el('div', { className: 'detail-actions' }, [
                    el('button', {
                        type: 'button', className: info.remote?.hub?.active ? 'btn btn-vlc' : 'btn btn-play',
                        text: info.remote?.hub?.active ? t('Stop Hub HTTPS Link') : t('Start Hub HTTPS Link'),
                        onClick: async () => {
                            statusMsg.textContent = t('Connecting Cloudflare Tunnel…');
                            try {
                                await callApi('/api/remote/toggle', { target: 'hub', enabled: !info.remote?.hub?.active });
                                openRemoteModal();
                            } catch (e) { statusMsg.textContent = e.message; }
                        },
                    }),
                    el('button', {
                        type: 'button', className: info.remote?.jellyfin?.active ? 'btn btn-vlc' : 'btn btn-play',
                        text: info.remote?.jellyfin?.active ? t('Stop Jellyfin HTTPS Link') : t('Start Jellyfin HTTPS Link'),
                        onClick: async () => {
                            statusMsg.textContent = t('Connecting Cloudflare Tunnel…');
                            try {
                                await callApi('/api/remote/toggle', { target: 'jellyfin', enabled: !info.remote?.jellyfin?.active });
                                openRemoteModal();
                            } catch (e) { statusMsg.textContent = e.message; }
                        },
                    }),
                ]),
                info.remote?.hub?.public_url ? qrBlock(info.remote.hub.public_url) : null,
                info.remote?.jellyfin?.public_url ? qrBlock(info.remote.jellyfin.public_url) : null,
                statusMsg,
            ]);

            const autoCheck = el('input', { type: 'checkbox' });
            autoCheck.checked = Boolean(info.autostart);
            autoCheck.addEventListener('change', () => callApi('/api/system/autostart', { enabled: autoCheck.checked }));
            const sysRow = el('label', { className: 'check-label' }, [
                autoCheck,
                el('span', { text: `${t('Start automatically with Windows')} · ${info.nvenc ? 'NVENC RTX Active' : 'CPU H.264'}` }),
            ]);

            openModal(t('TV & Remote Access'), [el('div', { className: 'remote-grid' }, [lanBox, remoteBox]), sysRow], 'remote');
        } catch (err) {
            openModal(t('TV & Remote Access'), [el('p', { className: 'notice', 'data-error': 'true', text: err.message })], 'remote');
        }
    }
    root.openRemoteModal = openRemoteModal;

    async function playCatalogItem(itemId, startSeconds = 0) {
        try {
            const data = await callApi('/api/catalog/open', { id: itemId });
            currentCatalogItemId = data.item_id || itemId;
            currentNextEpisodeId = data.next_episode_id || '';
            currentAddonNextContext = null;
            pendingResumeSeconds = Number(startSeconds || 0);
            if ($('nextEpisodeButton')) $('nextEpisodeButton').hidden = !currentNextEpisodeId;
            setHubView('player');
            await root.loadDownloadedVideo?.(data.source, data.filename, pendingResumeSeconds > 10 ? pendingResumeSeconds : 0);
            if (data.local_subtitles?.length && typeof root.addSubtitle === 'function') {
                for (const sub of data.local_subtitles) {
                    root.addSubtitle('libsub-' + sub.id, sub.label, { url: '/api/catalog/subtitle?id=' + encodeURIComponent(sub.id) });
                }
                if ($('selectSub')) {
                    $('selectSub').value = 'libsub-' + data.local_subtitles[0].id;
                    await root.onSubtitleChange?.();
                }
            }
        } catch (err) {
            if ($('playerStatus')) root.AppUI?.text($('playerStatus'), err.message);
        }
    }
    root.playCatalogItem = playCatalogItem;

    async function playNextAddonEpisode() {
        const ctx = currentAddonNextContext;
        if (!ctx?.nextEp) return;
        const btn = $('nextEpisodeButton');
        if (btn) btn.disabled = true;
        try {
            const data = await callApi(`/api/stremio/streams?kind=series&id=${encodeURIComponent(ctx.nextEp.id)}`);
            const playable = (data.streams || []).filter(s => s.playable);
            const best = playable.find(s => ctx.bingeGroup && s.binge_group === ctx.bingeGroup)
                || playable.find(s => ctx.preferPt && s.pt_audio)
                || playable[0];
            if (best) await playAddonStream(best, ctx.meta, ctx.nextEp);
            else if ($('playerStatus')) root.AppUI?.text($('playerStatus'), t('No playable HTTP/Debrid streams returned for this title.'));
        } catch (err) {
            if ($('playerStatus')) root.AppUI?.text($('playerStatus'), err.message);
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    async function saveChosenSubtitleToLibrary() {
        const subId = $('searchResults')?.value;
        const src = root.getCurrentLocalSource?.();
        if (!src || !subId) {
            root.notice?.('searchStatus', 'Select a library video and a subtitle first.', true);
            return;
        }
        if ($('saveSubtitleLibraryButton')) $('saveSubtitleLibraryButton').disabled = true;
        try {
            await callApi('/api/subtitles/save', { source: src, subtitle_id: subId, language: $('searchLanguage')?.value || 'pt-BR' });
            root.notice?.('searchStatus', 'Subtitle saved next to the video for Jellyfin and TV.');
            loadCatalog();
        } catch (err) {
            root.notice?.('searchStatus', err.message, true);
        } finally {
            if ($('saveSubtitleLibraryButton')) $('saveSubtitleLibraryButton').disabled = !$('searchResults')?.value;
        }
    }
    root.saveChosenSubtitleToLibrary = saveChosenSubtitleToLibrary;

    async function changeDownloadCategory(category) {
        try {
            const data = await callApi('/api/download/category', { category });
            if ($('downloadFolder') && data.folder) root.AppUI?.text($('downloadFolder'), data.folder);
        } catch (err) {
            root.notice?.('downloadStatus', err.message, true);
        }
    }
    root.changeDownloadCategory = changeDownloadCategory;

    function openJellyfin() {
        const host = root.location?.hostname || '127.0.0.1';
        root.open(`${root.location?.protocol || 'http:'}//${host}:8096`, '_blank', 'noopener');
    }
    root.openJellyfin = openJellyfin;

    function initHub() {
        const searchInput = $('hubSearchInput');
        if (searchInput) {
            searchInput.addEventListener('input', () => {
                clearTimeout(searchTimer);
                if (currentView === 'catalog') renderCatalog();
                else if (currentView === 'discover') searchTimer = setTimeout(loadDiscover, 350);
            });
            searchInput.addEventListener('keydown', e => {
                if (e.key === 'Enter' && currentView === 'catalog' && searchInput.value.trim()) {
                    setHubView('discover');
                    loadDiscover();
                }
            });
        }

        const video = $('mainVideo');
        if (video) {
            video.addEventListener('play', () => {
                clearInterval(progressTimer);
                progressTimer = setInterval(() => {
                    if (!currentCatalogItemId || video.paused) return;
                    const pos = root.getPlaybackPosition ? root.getPlaybackPosition() : (video.currentTime || 0);
                    const dur = root.getMovieDuration ? root.getMovieDuration() : (video.duration || 0);
                    if (pos > 3) callApi('/api/catalog/progress', { id: currentCatalogItemId, position: pos, duration: dur }).catch(() => {});
                }, 5000);
            });
            video.addEventListener('pause', () => {
                if (!currentCatalogItemId) return;
                const pos = root.getPlaybackPosition ? root.getPlaybackPosition() : (video.currentTime || 0);
                const dur = root.getMovieDuration ? root.getMovieDuration() : (video.duration || 0);
                if (pos > 3) callApi('/api/catalog/progress', { id: currentCatalogItemId, position: pos, duration: dur }).catch(() => {});
            });
        }

        if ($('nextEpisodeButton')) {
            $('nextEpisodeButton').addEventListener('click', () => {
                if (currentNextEpisodeId) playCatalogItem(currentNextEpisodeId, 0);
                else if (currentAddonNextContext) playNextAddonEpisode();
            });
        }

        refreshLibraryDownloads();

        root.addEventListener('streamchrome:preferences', () => {
            if (currentView === 'catalog') renderCatalog();
            else if (currentView === 'discover') renderDiscover();
        });
        root.addEventListener('keydown', e => {
            if (e.key === 'Escape') { closeModal(); return; }
            if (currentView === 'player' || ['INPUT', 'SELECT', 'TEXTAREA'].includes(e.target?.tagName)) return;
            if (!['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp'].includes(e.key)) return;
            const cards = Array.from(document.querySelectorAll('.media-card, .episode-row')).filter(node => node.offsetParent !== null);
            if (!cards.length) return;
            e.preventDefault();
            const idx = cards.indexOf(document.activeElement);
            if (idx === -1) { cards[0].focus(); return; }
            const step = (e.key === 'ArrowRight' || e.key === 'ArrowDown') ? 1 : -1;
            const next = cards[(idx + step + cards.length) % cards.length];
            next.focus();
            next.scrollIntoView({ block: 'nearest' });
        });

        setHubView('catalog');
    }

    if (typeof document !== 'undefined' && typeof document.createElement === 'function') {
        initHub();
    }
})(typeof window !== 'undefined' ? window : globalThis);
