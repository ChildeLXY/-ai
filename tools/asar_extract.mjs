#!/usr/bin/env node
/**
 * asar_extract.mjs —— 把 Electron app.asar 全量解包并生成包索引。
 *
 * 用法：
 *   node tools/asar_extract.mjs [asar路径] [输出目录]
 * 默认：
 *   asar = E:/DSH/resources/app.asar
 *   out  = D:/大肥鱼AI/dsh_pkgs
 *
 * 产物：
 *   <out>/...            保留 asar 内完整目录结构
 *   <out>/index.json     包索引（包名/版本/入口/依赖/inject）
 */
import { openSync, readSync, mkdirSync, writeFileSync, readFileSync, copyFileSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'

const ASAR = process.argv[2] || 'E:/DSH/resources/app.asar'
const OUT = process.argv[3] || 'D:/大肥鱼AI/dsh_pkgs'
// unpacked 文件不在 asar 数据区，落在同级的 app.asar.unpacked 目录
const UNPACKED_ROOT = join(dirname(ASAR), 'app.asar.unpacked')

// ---- asar 头解析（Chromium pickle：8 字节前缀 + JSON 头 + 文件数据）----
const fd = openSync(ASAR, 'r')
const pre = Buffer.alloc(8)
readSync(fd, pre, 0, 8, 0)
const headerSize = pre.readUInt32LE(4)
const hb = Buffer.alloc(headerSize)
readSync(fd, hb, 0, headerSize, 8)
const jsonLen = hb.readUInt32LE(4)
const header = JSON.parse(hb.toString('utf8', 8, 8 + jsonLen))
const dataBase = 8 + headerSize

function walk(node, path, out) {
  if (!node.files) {
    out.push({ path, node })
    return
  }
  for (const [k, v] of Object.entries(node.files)) {
    walk(v, path ? path + '/' + k : k, out)
  }
}
const all = []
walk(header, '', all)

// ---- 全量解包 ----
const madeDirs = new Set()
function ensureDir(d) {
  if (madeDirs.has(d)) return
  mkdirSync(d, { recursive: true })
  madeDirs.add(d)
}

let t0 = Date.now()
let count = 0
let unpackedCount = 0
for (const f of all) {
  const target = join(OUT, f.path)
  ensureDir(dirname(target))
  if (f.node.unpacked === true) {
    // 从 app.asar.unpacked 复制真实文件
    const src = join(UNPACKED_ROOT, f.path)
    if (existsSync(src)) {
      copyFileSync(src, target)
    } else {
      console.log('警告：unpacked 源文件缺失:', src)
      writeFileSync(target, Buffer.alloc(0))
    }
    unpackedCount++
  } else {
    const buf = Buffer.alloc(f.node.size)
    readSync(fd, buf, 0, f.node.size, dataBase + Number(f.node.offset))
    writeFileSync(target, buf)
  }
  count++
  if (count % 5000 === 0) console.log(`已解包 ${count}/${all.length}`)
}
console.log(`解包完成：${count} 个文件（其中 unpacked 复制 ${unpackedCount}），耗时 ${((Date.now() - t0) / 1000).toFixed(1)}s`)

// ---- 生成包索引 ----
const pkgFiles = all.filter((f) => f.path.endsWith('/package.json') || f.path === 'package.json')

function readText(f) {
  const buf = Buffer.alloc(f.node.size)
  readSync(fd, buf, 0, f.node.size, dataBase + Number(f.node.offset))
  return buf.toString('utf8')
}

// 从插件入口源码里静态抽 inject 声明（cordis 依赖注入）
function extractInject(mainRelPath) {
  const mainFile = all.find((f) => f.path === mainRelPath)
  if (!mainFile) return []
  let src = ''
  try {
    src = readText(mainFile)
  } catch {
    return []
  }
  const m = src.match(/(?:static\s+)?inject\s*=\s*(\[[\s\S]*?\])/)
  if (!m) return []
  const strs = [...m[1].matchAll(/["'`]([A-Za-z0-9_.\-/]+)["'`]/g)].map((x) => x[1])
  return [...new Set(strs)]
}

const packages = {}
for (const pf of pkgFiles) {
  let pkg
  try {
    pkg = JSON.parse(readText(pf))
  } catch {
    continue
  }
  if (!pkg.name) continue
  const pkgDir = pf.path === 'package.json' ? '' : dirname(pf.path)
  packages[pkg.name] = {
    version: pkg.version || '',
    path: pkgDir,
    main: pkg.main || (pkg.exports ? '' : 'index.js'),
    dependencies: pkg.dependencies || {},
    peerDependencies: pkg.peerDependencies || {},
    inject: pkgDir && pkg.main ? extractInject(join(pkgDir, pkg.main).replace(/\\/g, '/')) : [],
  }
}

const index = {
  generatedAt: new Date().toISOString(),
  asar: ASAR,
  totalFiles: all.length,
  packageCount: Object.keys(packages).length,
  packages,
}
writeFileSync(join(OUT, 'index.json'), JSON.stringify(index, null, 2))
console.log(`索引完成：${index.packageCount} 个包 → index.json`)

// 统计 @deepseek-ai 包与带 inject 的数量
const deep = Object.keys(packages).filter((n) => n.startsWith('@deepseek-ai/'))
const withInject = deep.filter((n) => packages[n].inject.length > 0)
console.log(`@deepseek-ai 包：${deep.length}，其中声明了 inject 的：${withInject.length}`)
