import { register } from 'node:module'
register('./loader-hooks.mjs', import.meta.url)
const mod = await import('file:///E:/DSH-home/profiles/desktop/node_modules/dsh-soul-md/index.js')
console.log('exports:', Object.keys(mod).slice(0, 12))
const C = mod.Config
console.log('Config type:', typeof C, '| keys:', C && Object.keys(C).slice(0, 15))
console.log('has ~standard:', C && Boolean(C['~standard']))
console.log('has validate:', C && typeof C.validate)
