import assert from 'node:assert'
const mod = await import('../engine-plugin/catalog-v2.mjs')
assert.equal(typeof mod.apply, 'function')
assert.equal(mod.name, 'catalog-v2')
console.log('plugin ok')
