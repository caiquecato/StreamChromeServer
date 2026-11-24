(function (root) {
    'use strict';
    // Compact ISO-18004 Byte-mode QR Code (Versions 1-6, ECC-L) SVG renderer.
    const VERSIONS = [
        { v: 1, size: 21, cap: 17, ec: 7, align: [] },
        { v: 2, size: 25, cap: 32, ec: 10, align: [6, 18] },
        { v: 3, size: 29, cap: 53, ec: 15, align: [6, 22] },
        { v: 4, size: 33, cap: 78, ec: 20, align: [6, 26] },
        { v: 5, size: 37, cap: 106, ec: 26, align: [6, 30] },
        { v: 6, size: 41, cap: 134, ec: 18, blocks: 2, align: [6, 34] },
    ];
    const EXP = new Uint8Array(512), LOG = new Uint8Array(256);
    for (let i = 0, v = 1; i < 255; i++) {
        EXP[i] = EXP[i + 255] = v;
        LOG[v] = i;
        v = v & 0x80 ? (v << 1) ^ 0x11d : v << 1;
    }
    const mul = (a, b) => (a && b ? EXP[LOG[a] + LOG[b]] : 0);

    function rsGenerator(degree) {
        let g = [1];
        for (let i = 0; i < degree; i++) {
            const next = new Array(g.length + 1).fill(0);
            for (let j = 0; j < g.length; j++) {
                next[j] ^= g[j];
                next[j + 1] ^= mul(g[j], EXP[i]);
            }
            g = next;
        }
        return g;
    }

    function rsRemainder(data, ecCount) {
        const gen = rsGenerator(ecCount);
        const rem = new Uint8Array(ecCount);
        for (const byte of data) {
            const factor = byte ^ rem[0];
            rem.copyWithin(0, 1);
            rem[ecCount - 1] = 0;
            for (let j = 0; j < ecCount; j++) rem[j] ^= mul(gen[j + 1], factor);
        }
        return rem;
    }

    function buildMatrix(text) {
        const bytes = new TextEncoder().encode(String(text || ''));
        const spec = VERSIONS.find(s => bytes.length <= s.cap);
        if (!spec) return null;
        const blocks = spec.blocks || 1;
        const dataCap = spec.cap + 2;
        const bits = [];
        const pushBits = (val, len) => { for (let i = len - 1; i >= 0; i--) bits.push((val >> i) & 1); };
        pushBits(0b0100, 4);
        pushBits(bytes.length, 8);
        for (const b of bytes) pushBits(b, 8);
        pushBits(0, Math.min(4, dataCap * 8 - bits.length));
        while (bits.length % 8) bits.push(0);
        const codewords = [];
        for (let i = 0; i < bits.length; i += 8) {
            let b = 0;
            for (let j = 0; j < 8; j++) b = (b << 1) | bits[i + j];
            codewords.push(b);
        }
        let pad = 0;
        while (codewords.length < dataCap) {
            codewords.push(pad % 2 === 0 ? 0xec : 0x11);
            pad++;
        }
        const blockSize = Math.floor(codewords.length / blocks);
        const dataBlocks = [], ecBlocks = [];
        for (let b = 0, offset = 0; b < blocks; b++) {
            const len = blockSize + (b >= blocks - (codewords.length % blocks) ? 1 : 0);
            const slice = codewords.slice(offset, offset + len);
            offset += len;
            dataBlocks.push(slice);
            ecBlocks.push(rsRemainder(slice, spec.ec));
        }
        const finalBytes = [];
        const maxLen = Math.max(...dataBlocks.map(b => b.length));
        for (let i = 0; i < maxLen; i++) for (const blk of dataBlocks) if (i < blk.length) finalBytes.push(blk[i]);
        for (let i = 0; i < spec.ec; i++) for (const blk of ecBlocks) finalBytes.push(blk[i]);

        const n = spec.size;
        const m = Array.from({ length: n }, () => new Int8Array(n).fill(-1));
        const res = Array.from({ length: n }, () => new Uint8Array(n));
        const setMod = (r, c, v, lock = true) => {
            if (r >= 0 && r < n && c >= 0 && c < n) { m[r][c] = v ? 1 : 0; if (lock) res[r][c] = 1; }
        };
        const finder = (r, c) => {
            for (let dr = -1; dr <= 7; dr++) for (let dc = -1; dc <= 7; dc++) {
                const inside = dr >= 0 && dr <= 6 && dc >= 0 && dc <= 6;
                const dark = inside && (dr === 0 || dr === 6 || dc === 0 || dc === 6 || (dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4));
                setMod(r + dr, c + dc, dark ? 1 : 0);
            }
        };
        finder(0, 0); finder(0, n - 7); finder(n - 7, 0);
        for (let i = 8; i < n - 8; i++) { setMod(6, i, i % 2 === 0); setMod(i, 6, i % 2 === 0); }
        setMod(n - 8, 8, 1);
        for (const r of spec.align) for (const c of spec.align) {
            if (res[r][c]) continue;
            for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++) {
                setMod(r + dr, c + dc, Math.max(Math.abs(dr), Math.abs(dc)) !== 1 ? 1 : 0);
            }
        }
        for (let i = 0; i < 9; i++) {
            if (i !== 6) { res[8][i] = 1; res[i][8] = 1; }
            if (i < 8) { res[8][n - 1 - i] = 1; res[n - 1 - i][8] = 1; }
        }
        let bitIdx = 0;
        const totalBits = finalBytes.length * 8;
        let dir = -1;
        for (let col = n - 1; col > 0; col -= 2) {
            if (col === 6) col--;
            for (let row = dir < 0 ? n - 1 : 0; row >= 0 && row < n; row += dir) {
                for (let k = 0; k < 2; k++) {
                    const c = col - k;
                    if (res[row][c]) continue;
                    const bit = bitIdx < totalBits ? (finalBytes[bitIdx >> 3] >> (7 - (bitIdx & 7))) & 1 : 0;
                    bitIdx++;
                    const masked = bit ^ ((row + c) % 2 === 0 ? 1 : 0);
                    m[row][c] = masked;
                }
            }
            dir = -dir;
        }
        // Format info: ECC-L (01) + Mask 0 (000) -> 0x77c4
        const fmt = 0x77c4;
        for (let i = 0; i < 15; i++) {
            const bit = (fmt >> (14 - i)) & 1;
            const r1 = i < 6 ? i : i === 6 ? 7 : i === 7 ? 8 : 14 - i < 6 ? 14 - i : 8;
            if (i < 6) setMod(8, i, bit);
            else if (i < 8) setMod(8, i + 1, bit);
            else setMod(14 - i, 8, bit);
            if (i < 7) setMod(n - 1 - i, 8, bit);
            else if (i === 7) setMod(8, n - 8, bit);
            else setMod(8, n - 15 + i, bit);
        }
        // Correct vertical format coords 0..7
        const fcoords = [[8,0],[8,1],[8,2],[8,3],[8,4],[8,5],[8,7],[8,8],[7,8],[5,8],[4,8],[3,8],[2,8],[1,8],[0,8]];
        const fcoords2 = [[n-1,8],[n-2,8],[n-3,8],[n-4,8],[n-5,8],[n-6,8],[n-7,8],[8,n-8],[8,n-7],[8,n-6],[8,n-5],[8,n-4],[8,n-3],[8,n-2],[8,n-1]];
        for (let i = 0; i < 15; i++) {
            const bit = (fmt >> (14 - i)) & 1;
            m[fcoords[i][0]][fcoords[i][1]] = bit;
            m[fcoords2[i][0]][fcoords2[i][1]] = bit;
        }
        return m;
    }

    function renderSvgElement(text, sizePx = 148) {
        const m = buildMatrix(text);
        const svgNS = 'http://www.w3.org/2000/svg';
        const svg = root.document.createElementNS(svgNS, 'svg');
        if (!m) return svg;
        const n = m.length;
        const vb = n + 4;
        svg.setAttribute('viewBox', `0 0 ${vb} ${vb}`);
        svg.setAttribute('width', String(sizePx));
        svg.setAttribute('height', String(sizePx));
        svg.setAttribute('role', 'img');
        svg.setAttribute('aria-label', String(text));
        const bg = root.document.createElementNS(svgNS, 'rect');
        bg.setAttribute('width', String(vb));
        bg.setAttribute('height', String(vb));
        bg.setAttribute('rx', '2');
        bg.setAttribute('fill', '#faf9f6');
        svg.appendChild(bg);
        let d = '';
        for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
            if (m[r][c] === 1) d += `M${c + 2},${r + 2}h1v1h-1z`;
        }
        const path = root.document.createElementNS(svgNS, 'path');
        path.setAttribute('d', d);
        path.setAttribute('fill', '#141412');
        svg.appendChild(path);
        return svg;
    }

    const api = { buildMatrix, renderSvgElement };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.QrSvg = api;
})(typeof window !== 'undefined' ? window : globalThis);
