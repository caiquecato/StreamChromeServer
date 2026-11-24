const assert = require('node:assert/strict');
const { buildMatrix } = require('../assets/qrcode.js');

for (const sample of ['http://192.168.1.3:8096', 'https://sample-cinema-hub.trycloudflare.com']) {
  const m = buildMatrix(sample);
  assert.ok(Array.isArray(m) && m.length >= 21);
  // Verify top-left 7x7 finder corners and separator
  assert.equal(m[0][0], 1);
  assert.equal(m[3][3], 1);
  assert.equal(m[1][1], 0);
  assert.equal(m[7][7], 0);
}
assert.equal(buildMatrix('x'.repeat(300)), null);
console.log('Offline QR matrix generation and finder invariants verified.');
