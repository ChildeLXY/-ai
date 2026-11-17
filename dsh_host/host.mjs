#!/usr/bin/env node
/**
 * dfy-dsh-host —— 大肥鱼AI 的「DSH 插件兼容宿主」
 *
 * 为什么需要它：
 *   DSH 的插件是跑在 DSH 进程里的 ESM 模块，通过 inject 声明依赖一堆宿主服务
 *   （systemPrompt / agents / tools / llm / sessions / webServer / timer …）。
 *   Python 没法直接执行它们，DSH 本体也不一定开着 —— 所以这里用真 cordis
 *   运行时 + 这些服务的最小实现，把插件原样加载起来。
 *
 * 它对外只做一件事：把「所有插件想往提示词里说的话」和「所有插件注册的工具」
 * 通过 HTTP 暴露出来，让大肥鱼AI 每轮聊天前拉一次。这样任何插件都自动对它生效。
 *
 * 用法：
 *   node host.mjs            # 前台跑，默认 127.0.0.1:6800
 *   DFY_DSH_PORT=6801 node host.mjs
 */
import { register } from 'node:module'
import { Context } from 'cordis'
import { createServer } from 'node:http'
import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs'
import { join, dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { homedir } from 'node:os'
import z from './node_modules/@deepseek-ai/schemastery/index.js'

// 先挂解析钩子，再加载插件：插件里的 @deepseek-ai/* 会落到本宿主的 shim 上
register('./loader-hooks.mjs', import.meta.url)

const HOST_DIR = dirname(fileURLToPath(import.meta.url))
// hindsight 长期记忆插件的配置：不管谁拉起 host，都保证它读到 D 盘那份，
// 连本机 http://127.0.0.1:8888 的 Hindsight server。
if (!process.env.HINDSIGHT_CONFIG) {
  const cfg = 'D:/hindsight-server/config/coding-agent.json'
  try { if (existsSync(cfg)) process.env.HINDSIGHT_CONFIG = cfg } catch {}
}
// 本机真实的 DSH 主目录（插件数据都在这里：心情、人设、贴纸库）
const DSH_HOME = process.env.DSH_HOME || 'E:/DSH-home'
// 插件查找顺序：先找转接口自己的目录（市场装的新插件都落这里，
// 完全不碰 DSH 的 profile，避免破坏它的 pnpm 链接结构），再找 DSH 原有的。
// 探测模式下，临时 stage 的 node_modules 要排在最前面，因为那时包还没挂进真实 profile。
const PLUGIN_ROOTS = [
  ...(process.env.DFY_PROBE_ROOT ? [process.env.DFY_PROBE_ROOT] : []),
  process.env.DFY_PLUGIN_DIR || join(HOST_DIR, 'plugins', 'node_modules'),
  process.env.DFY_PLUGIN_ROOT || join(DSH_HOME, 'profiles', 'desktop', 'node_modules'),
]

function findPluginRoot(name) {
  for (const root of PLUGIN_ROOTS) {
    if (existsSync(join(root, name))) return join(root, name)
  }
  return ''
}
const PORT = Number(process.env.DFY_DSH_PORT || 6800)
const HOST = process.env.DFY_DSH_HOST || '127.0.0.1'
const OLLAMA = process.env.DFY_OLLAMA || 'http://127.0.0.1:11434'

// 探测模式：市场安装前先在独立进程里只加载这一个插件，
// 把成功/失败写进结果文件后退出，不监听端口、不碰真实宿主。
const PROBE = process.env.DFY_PROBE === '1'
const PROBE_NAME = (process.env.DFY_PROBE_PLUGIN || '').trim()
const PROBE_RESULT = process.env.DFY_PROBE_RESULT || ''

// 插件读写 DSH_HOME 下的数据，必须指对，否则读到的是另一份心情/人设
process.env.DSH_HOME = DSH_HOME

const LOG_PATH = join(HOST_DIR, 'host.log')
function log(msg, level = 'INFO') {
  const line = `[${new Date().toLocaleTimeString('zh-CN', { hour12: false })}][${level}] ${msg}`
  try { console.log(line) } catch {}
  try { writeFileSync(LOG_PATH, line + '\n', { flag: 'a' }) } catch {}
}

// ---------------------------------------------------------------- 宽容兜底
// 插件的服务面很宽，第一版对没实现的方法一律 no-op 并记日志，
// 保证「加载不崩、能力逐步补」，而不是一上来就 ERR_MODULE_NOT_FOUND。
function tolerant(name, impl = {}) {
  return new Proxy(impl, {
    get(target, key) {
      if (key in target) return target[key]
      if (typeof key === 'symbol') return undefined
      if (key === 'then') return undefined
      return (...args) => {
        log(`${name}.${String(key)}(...) 未实现，已忽略`, 'WARN')
        return undefined
      }
    },
    has() { return true },
  })
}

// ---------------------------------------------------------------- 服务实现
/** systemPrompt：每个 agent 一轮对话前拼提示词的地方。插件往这里塞片段。 */
function createSystemPrompt() {
  const fragments = new Map()
  return {
    fragments,
    context({ name, order = 100, text, interpolate, complete }) {
      if (!name) return () => {}
      fragments.set(name, { order, text, kind: 'context', interpolate, complete })
      return () => fragments.delete(name)
    },
    /** 正式段落（进系统提示词主体）。对我们的网关来说也是"注入文本"，只是排序位更靠前。 */
    section({ name, order = 100, text, interpolate, complete }) {
      if (!name) return () => {}
      fragments.set(name, { order, text, kind: 'section', interpolate, complete })
      return () => fragments.delete(name)
    },
    /** 收集当前所有片段（按 order 排序），返回纯文本数组 */
    collect() {
      const rows = [...fragments.entries()].map(([name, it]) => {
        let body = ''
        try {
          body = typeof it.text === 'function' ? it.text() : String(it.text || '')
        } catch (e) {
          log(`systemPrompt 片段 ${name} 取值失败：${e}`, 'WARN')
        }
        return { name, order: it.order ?? 100, kind: it.kind || 'context', text: String(body || '').trim() }
      })
      return rows.filter((r) => r.text).sort((a, b) => a.order - b.order)
    },
  }
}

/** tools：插件注册工具的地方。 */
function createTools() {
  const map = new Map()
  return {
    map,
    register(tool) {
      if (!tool || !tool.name) return () => {}
      map.set(tool.name, tool)
      return () => map.delete(tool.name)
    },
    list() {
      return [...map.values()].map((t) => ({
        name: t.name,
        description: t.description || '',
        parameters: t.parameters || {},
      }))
    },
    async call(name, args = {}) {
      const tool = map.get(name)
      if (!tool) throw new Error(`没有这个工具：${name}`)
      const out = await tool.execute(args, tolerant('exec'))
      return out
    },
  }
}

/** webServer：插件开自己的 HTTP 路由（界面面板用）。 */
function createWebServer() {
  const routes = []
  const add = (method) => (path, handler) => {
    routes.push({ method, path, handler })
    return () => {
      const i = routes.findIndex((r) => r.path === path && r.method === method)
      if (i >= 0) routes.splice(i, 1)
    }
  }
  return {
    routes,
    // DSH 的写法：ctx.webServer.register({ kind, path, handler }) → 返回 dispose
    register(route) {
      if (!route || !route.path) return () => {}
      const method = String(route.method || (route.kind === 'prefix' ? 'ALL' : 'ALL')).toUpperCase()
      routes.push({ method, path: route.path, handler: route.handler, kind: route.kind })
      return () => {
        const i = routes.findIndex((r) => r.path === route.path && r.handler === route.handler)
        if (i >= 0) routes.splice(i, 1)
      }
    },
    get: add('GET'), post: add('POST'), put: add('PUT'), delete: add('DELETE'), all: add('ALL'), add,
  }
}

/** llm：插件用它做判断（例如心情涨跌）。走本机 ollama，与大肥鱼同一个模型。 */
function createLlm(defaultModel = 'qwen3.6:35b') {
  const pickModel = (m) => (m && /^(qwen|deepseek|llama|gemma|mistral)/i.test(m) ? m : defaultModel)

  async function chat(messages, opts = {}) {
    const body = {
      model: pickModel(opts.model),
      messages,
      stream: false,
      think: opts.think === true,
      keep_alive: '30m',
      options: { temperature: opts.temperature ?? 0.7, num_predict: opts.maxTokens ?? 512,
                num_ctx: 3072, num_batch: 512, num_thread: 8, num_gpu: 99, num_keep: 48 },
    }
    const r = await fetch(`${OLLAMA}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const j = await r.json()
    return ((j.message || {}).content || '').replace(/<think>[\s\S]*?<\/think>/g, '').trim()
  }

  /** DSH 的调用形态：for await (const chunk of ctx.llm.stream({...})) */
  async function* stream(opts = {}) {
    const msgs = []
    if (opts.system) {
      msgs.push({ role: 'system', content: typeof opts.system === 'string' ? opts.system : JSON.stringify(opts.system) })
    }
    for (const m of opts.messages || []) {
      const text = Array.isArray(m.content)
        ? m.content.filter((b) => b && b.type === 'text').map((b) => b.text).join('')
        : String(m.content ?? '')
      msgs.push({ role: m.role || 'user', content: text })
    }
    const body = {
      model: pickModel(opts.model),
      messages: msgs,
      stream: true,
      think: false,
      keep_alive: '30m',
      options: { temperature: opts.temperature ?? 0.7, num_predict: opts.maxTokens ?? 800,
                num_ctx: 3072, num_batch: 512, num_thread: 8, num_gpu: 99, num_keep: 48 },
    }
    try {
      const r = await fetch(`${OLLAMA}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      if (!r.ok || !r.body) {
        yield { type: 'finish', reason: { kind: 'error', failure: { message: `ollama HTTP ${r.status}` } } }
        return
      }
      const reader = r.body.getReader()
      const dec = new TextDecoder()
      let buf = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buf += dec.decode(value, { stream: true })
        const lines = buf.split('\n')
        buf = lines.pop() || ''
        for (const line of lines) {
          const s = line.trim()
          if (!s) continue
          try {
            const j = JSON.parse(s)
            const piece = (j.message && j.message.content) || ''
            if (piece) yield { type: 'text-delta', text: piece }
            if (j.done) yield { type: 'finish', reason: { kind: 'stop' } }
          } catch {}
        }
      }
      yield { type: 'finish', reason: { kind: 'stop' } }
    } catch (e) {
      yield { type: 'finish', reason: { kind: 'error', failure: { message: String(e) } } }
    }
  }

  return tolerant('llm', {
    stream,
    chat,
    complete: (prompt, opts) => chat([{ role: 'user', content: prompt }], opts),
    createUserMessage: (text) => ({ role: 'user', content: text }),
    createAssistantMessage: (text) => ({ role: 'assistant', content: text }),
    listProviders: () => [{ id: 'ollama', name: 'Ollama（本机）' }],
    listModels: async () => [{ id: defaultModel, name: defaultModel }],
  })
}

/** timer / interval：cordis 插件习惯的定时器，随 ctx 销毁自动清理。 */
function createTimer() {
  const timers = new Set()
  const wrap = (fn) => {
    const h = { id: null, dispose() { clearTimeout(h.id); clearInterval(h.id); timers.delete(h) } }
    timers.add(h)
    return fn(h)
  }
  return {
    setTimeout(fn, ms) { return wrap((h) => { h.id = setTimeout(() => { timers.delete(h); fn() }, ms); return h }) },
    setInterval(fn, ms) { return wrap((h) => { h.id = setInterval(fn, ms); return h }) },
    clear(h) { h && h.dispose && h.dispose() },
    dispose() { for (const h of [...timers]) h.dispose() },
  }
}

/** settings：插件存自己的配置。落盘到 <DSH_HOME>/dfy-host-settings.json。 */
function createSettings() {
  const path = join(DSH_HOME, 'dfy-host-settings.json')
  let data = {}
  try { data = JSON.parse(readFileSync(path, 'utf8')) } catch {}
  return {
    get(key, def) { return key in data ? data[key] : def },
    async update(patch = {}) {
      Object.assign(data, patch)
      try { writeFileSync(path, JSON.stringify(data, null, 2)) } catch (e) { log('settings 落盘失败：' + e, 'WARN') }
    },
    all() { return { ...data } },
  }
}

// ---------------------------------------------------------------- 宿主装配
const root = new Context()

function provide(name, impl) {
  try { root.provide(name) } catch {}
  try { root.set(name, impl) } catch (e) { log(`服务 ${name} 注册失败：${e}`, 'WARN') }
}

// 虚拟 agent：大肥鱼QQ 这一路。插件按 agent 挂钩子，所以必须有一个真 ctx。
const agentCtx = root.extend()
const agentPrompt = createSystemPrompt()
const agentTools = createTools()
agentCtx.provide('systemPrompt'); agentCtx.set('systemPrompt', agentPrompt)
agentCtx.provide('tools'); agentCtx.set('tools', agentTools)
agentCtx.provide('sessions'); agentCtx.set('sessions', tolerant('sessions'))

// 会话事件流水：心情这类插件要靠它拿「这一轮用户说了啥 / 她回了啥」。
// 只保留最近 60 条，够插件回溯，也不占内存。
const EVENTS = []
let memoryInjection = '' // hindsight 最近一次召回到注入文本的缓存，/context 时并进提示词
const AGENT = {
  id: 'dfy-qq',
  sessionId: 'dfy-qq-main',
  title: '大肥鱼 · QQ',
  workspace: 'D:/大肥鱼AI',
  ctx: root,
  // 插件判断时会从这里取"用哪个模型"；不给就整轮跳过
  options: { provider: 'ollama', model: process.env.DFY_LLM_MODEL || 'qwen3.6:35b' },
  session: {
    // 插件从 session.id 取会话标识来匹配绑定（心情、主动搭话都靠它）
    id: 'dfy-qq-main',
    header: { id: 'dfy-qq-main', cwd: 'D:/大肥鱼AI' },
    // 插件判断时优先从这里取模型；options 是第二条路，两条都给上
    requestHeader: () => ({ config: { provider: 'ollama', model: process.env.DFY_LLM_MODEL || 'qwen3.6:35b' } }),
    snapshotEvents: () => EVENTS.slice(),
  },
}

const webServer = createWebServer()
const timer = createTimer()
const settings = createSettings()
const llm = createLlm(process.env.DFY_LLM_MODEL || 'qwen3.6:35b')

provide('systemPrompt', agentPrompt)
provide('tools', agentTools)
provide('webServer', webServer)
provide('timer', timer)
// 插件里习惯写 ctx.interval(fn, ms) / ctx.setTimeout(fn, ms)；cordis 把它们当服务名，
// 所以这里按服务注册一份，指向同一个 timer（随宿主退出一起清理）。
// 插件里习惯写 ctx.interval(fn, ms)（通常在 ctx.effect 里），要求返回「清理函数」——
// 返回带 dispose 的对象会让 cordis 判为 Invalid effect，所以这里返回函数。
provide('interval', (fn, ms) => {
  const id = setInterval(fn, ms)
  return () => clearInterval(id)
})
provide('timeout', (fn, ms) => {
  const id = setTimeout(fn, ms)
  return () => clearTimeout(id)
})
provide('settings', settings)
provide('llm', llm)
provide('agents', tolerant('agents', {
  list: () => [AGENT],
  roots: () => ['D:/大肥鱼AI'],
  get: (id) => (id === AGENT.id ? AGENT : undefined),
  current: () => AGENT,
}))
provide('sessions', tolerant('sessions', {
  list: () => [AGENT],
  get: () => AGENT,
  current: () => AGENT,
}))
provide('sessionTitle', tolerant('sessionTitle'))
provide('workspaceRegistry', tolerant('workspaceRegistry', { list: () => ['D:/大肥鱼AI'] }))

// ---------------------------------------------------------------- 插件加载
/** 插件名单：host-plugins.json 里的 enabled 列表；没有就用默认几个。 */
function loadManifest() {
  const path = join(HOST_DIR, 'host-plugins.json')
  if (!existsSync(path)) {
    const seed = { plugins: ['dsh-feiyu-mood', 'dsh-soul-md', 'dsh-nekowhale-stickers'] }
    try { writeFileSync(path, JSON.stringify(seed, null, 2)) } catch {}
    return seed
  }
  try { return JSON.parse(readFileSync(path, 'utf8')) } catch { return { plugins: [] } }
}

const loaded = new Map() // name → { ok, error, at }
const manifest = loadManifest()
// 探测模式忽略真实名单，只看目标这一个
if (PROBE) manifest.plugins = [PROBE_NAME]

async function loadPlugins() {
  for (const name of manifest.plugins || []) {
    const dir = findPluginRoot(name)
    if (!dir) {
      loaded.set(name, { ok: false, error: `找不到插件目录：${name}` })
      log(`跳过 ${name}：目录不存在`, 'WARN')
      continue
    }
    let main = 'index.js'
    try {
      const pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'))
      main = pkg.main || pkg.exports?.['.']?.default || pkg.exports?.['.'] || 'index.js'
      if (typeof main === 'object') main = main.default || 'index.js'
    } catch {}
    // 有些插件（如 hindsight）的 DSH 入口不在 main，而在 ./dsh 导出，
    // 用 host-plugins.json 的 entryOverrides 指定，避免改插件包本身。
    const override = manifest.entryOverrides?.[name]
    if (override) main = override
    log(`${name} 入口：${main}`)
    try {
      const mod = await import(pathToFileURL(resolve(dir, main)).href)
      const comp = mod.default && mod.default.apply ? mod.default : mod
      // 插件用 schemastery 描述 Config，cordis 不替我们补默认值 —— 自己先算一遍，
      // 否则插件拿到 undefined，很多插件会在 Object.entries(config) 上直接崩。
      let cfg = {}
      try { cfg = (mod.Config && z.resolve(mod.Config)) || {} } catch (e) { log(`${name} 配置解析失败：${e}`, 'WARN') }

      // soul-md 的人设卡存在 settings 里（cards/active），而本机从来没在 DSH 界面里配过，
      // 所以这里把 DSH_HOME/soul-md/*.md 这些现成人设文件导进去，否则注入文本是空的。
      if (name === 'dsh-soul-md') {
        try {
          const dir = join(DSH_HOME, 'soul-md')
          const cards = {}
          if (existsSync(dir)) {
            for (const f of readdirSync(dir)) {
              if (!f.endsWith('.md') || f === 'global.md') continue
              const text = readFileSync(join(dir, f), 'utf8').trim()
              if (text) cards[f.replace(/\.md$/i, '')] = text
            }
          }
          cfg.cards = { ...cards, ...(cfg.cards || {}) }
          if (!cfg.active || !cfg.cards[cfg.active]) cfg.active = cards['大肥鱼'] ? '大肥鱼' : (Object.keys(cfg.cards)[0] || '')
          log(`soul-md 人设卡：${Object.keys(cfg.cards).join(', ')}（当前 ${cfg.active || '无'}）`)
        } catch (e) {
          log('导入 soul-md 人设失败：' + e, 'WARN')
        }
      }

      await root.use(comp, cfg)
      loaded.set(name, { ok: true, at: Date.now(), main })
      log(`插件已加载：${name}`)
    } catch (e) {
      loaded.set(name, { ok: false, error: String(e && e.stack || e).slice(0, 800) })
      log(`插件加载失败 ${name}：${e}`, 'ERROR')
    }
  }
}

// ---------------------------------------------------------------- HTTP 网关
function json(res, code, obj) {
  const body = JSON.stringify(obj, null, 2)
  res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Access-Control-Allow-Origin': '*' })
  res.end(body)
}

const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || '127.0.0.1'}`)
  const path = url.pathname

  if (req.method === 'OPTIONS') {
    res.writeHead(204, {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    })
    return res.end()
  }

  try {
    if (path === '/health') {
      return json(res, 200, {
        ok: true,
        host: HOST,
        port: PORT,
        dshHome: DSH_HOME,
        plugins: Object.fromEntries(loaded),
        fragments: agentPrompt.collect().map((f) => f.name),
        fragmentsRaw: [...agentPrompt.fragments.entries()].map(([k, v]) => ({
          name: k,
          order: v.order,
          kind: v.kind,
          len: (() => {
            try { return String(typeof v.text === 'function' ? v.text() : v.text || '').length } catch (e) { return `err:${e.message}` }
          })(),
        })),
        tools: agentTools.list().map((t) => t.name),
      })
    }

    // 插件往提示词里注入的全部内容（大肥鱼每轮聊天前拉这个）
    if (path === '/context') {
      // 先让中间件类插件（hindsight）做一次记忆召回：它会在 preStep 里
      // 把相关长期记忆作为一条注入消息追加到 decision.messages。
      // 用最近一条用户消息当召回线索（大肥鱼AI 目前调 /context 不带当前 prompt）。
      try {
        const ac = new AbortController()
        const lastUser = [...EVENTS].reverse().find((e) => e && e.type === 'user/message')
        const tmpl = lastUser ? [lastUser.data || lastUser.payload] : []
        const decision = await agentCtx.waterfall('agent/pre-step',
          { agent: AGENT, signal: ac.signal },
          async () => ({ kind: 'enter', messages: tmpl }))
        const injectMsgs = (decision?.messages || []).filter((m) =>
          m && m.source && String(m.source.kind || '').includes('hindsight'))
        const injectText = injectMsgs.map((m) => (m.content || []).map((b) => b.text || '').join('\n')).join('\n').trim()
        if (injectText && injectText !== memoryInjection) {
          memoryInjection = injectText
          log(`hindsight 记忆召回 ${injectText.length} 字`)
        }
      } catch (e) {
        log('pre-step 召回失败：' + e, 'WARN')
      }
      const rows = agentPrompt.collect()
      if (memoryInjection) {
        rows.push({ name: 'hindsight-memory', order: 5, kind: 'context', text: memoryInjection })
        rows.sort((a, b) => (a.order ?? 100) - (b.order ?? 100))
      }
      return json(res, 200, {
        ok: true,
        text: rows.map((r) => r.text).join('\n'),
        parts: rows,
      })
    }

    if (path === '/tools') {
      return json(res, 200, { ok: true, tools: agentTools.list() })
    }

    if (path === '/tool' && req.method === 'POST') {
      const chunks = []
      for await (const c of req) chunks.push(c)
      const { name, args } = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')
      try {
        const out = await agentTools.call(name, args || {})
        return json(res, 200, { ok: true, result: out })
      } catch (e) {
        return json(res, 200, { ok: false, error: String(e) })
      }
    }

    // 一轮对话结束：把「用户说的」和「她回的」告诉插件。
    // 心情插件据此判断涨跌；source.kind='qq' 正好在它的外网桥白名单里。
    if (path === '/turn' && req.method === 'POST') {
      const chunks = []
      for await (const c of req) chunks.push(c)
      const { user = '', reply = '', source = 'qq' } = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')
      const userMsg = { content: [{ type: 'text', text: String(user) }], source: { kind: source } }
      try { agentCtx.emit('agent/inbox/inserted', { message: userMsg }) } catch (e) { log('emit inbox 失败：' + e, 'WARN') }
      // 事件双字段：payload 给老插件（心情/记忆，读 source.kind='qq'），
      // data 给 hindsight（它只认 event.data 且要求 user/message 的 source.kind==='user'）。
      // 老插件一律 `payload !== undefined ? payload : data`，所以优先拿到 payload，不受影响。
      const hindsightUser = { content: [{ type: 'text', text: String(user) }], source: { kind: 'user' } }
      EVENTS.push({ type: 'user/message', payload: userMsg, data: hindsightUser })
      if (reply) {
        const replyMsg = { content: [{ type: 'text', text: String(reply) }] }
        EVENTS.push({ type: 'assistant/message', payload: replyMsg, data: { message: replyMsg } })
      }
      while (EVENTS.length > 60) EVENTS.shift()
      try { agentCtx.emit('agent/turn-stopping', { agent: AGENT }) } catch (e) { log('emit turn-stopping 失败：' + e, 'WARN') }
      // 临时诊断：确认事件真的到了、插件也能从我们的会话里读出内容
      if (process.env.DFY_DEBUG) {
        let hits = 0
        agentCtx.on('agent/turn-stopping', () => { hits++ })
        agentCtx.emit('agent/turn-stopping', { agent: AGENT })
        setTimeout(() => log(`探针：turn-stopping 命中 ${hits} 次`), 1500)
        try {
          const fm = await import(pathToFileURL(join(findPluginRoot('dsh-feiyu-mood'), 'lib', 'index.js')).href)
          const info = fm.lastUserTurnOf(AGENT.session)
          log('探针 lastUserTurnOf = ' + JSON.stringify(info))
        } catch (e) { log('探针失败：' + e, 'WARN') }
      }
      return json(res, 200, { ok: true, events: EVENTS.length })
    }

    // 插件自己开的路由（界面面板 / 状态查询）
    const route = webServer.routes.find((r) => r.path === path && (r.method === 'ALL' || r.method === req.method))
    if (route) {
      const chunks = []
      for await (const c of req) chunks.push(c)
      req.body = Buffer.concat(chunks)
      req.query = Object.fromEntries(url.searchParams)
      return route.handler(req, res)
    }

    json(res, 404, { ok: false, error: 'not found' })
  } catch (e) {
    log(`HTTP ${path} 出错：${e}`, 'ERROR')
    json(res, 500, { ok: false, error: String(e) })
  }
})

await loadPlugins()

if (PROBE) {
  const st = loaded.get(PROBE_NAME) || { ok: false, error: '插件未进入加载流程' }
  const result = {
    ok: !!st.ok,
    name: PROBE_NAME,
    main: st.main || '',
    error: st.error ? String(st.error).slice(0, 600) : '',
  }
  try {
    writeFileSync(PROBE_RESULT, JSON.stringify(result, null, 2))
  } catch (e) {
    log('探测结果写不出来：' + e, 'ERROR')
  }
  log(`探测 ${PROBE_NAME}：${result.ok ? '通过' : '不通过'}${result.error ? '（' + result.error.split('\n')[0] + '）' : ''}`)
  process.exit(result.ok ? 0 : 1)
}

// hindsight 这类插件在 session-start 时做初始化/冷启动 seed。重启 host 后补发一次。
try { agentCtx.emit('agent/session-start', { agent: AGENT }) } catch (e) { log('emit session-start 失败：' + e, 'WARN') }

server.listen(PORT, HOST, () => {
  log(`兼容宿主就绪 http://${HOST}:${PORT}（DSH_HOME=${DSH_HOME}）`)
  log(`已加载插件：${[...loaded].filter(([, v]) => v.ok).map(([k]) => k).join(', ') || '（无）'}`)
})
