/**
 * 模块解析钩子：把插件的 `@deepseek-ai/*` 依赖重定向到本宿主的 shim。
 *
 * 为什么需要：插件装在 DSH 的 profile 里（E:\DSH-home\profiles\desktop\node_modules），
 * 它们 import 的内置包原本由 DSH 从 app 内部提供、磁盘上没有。Node 会从插件自身目录
 * 向上找 node_modules，够不到我们这里的 shim —— 所以在这一层截住。
 * 好处：完全不动 DSH 的目录，插件源码保持原样。
 */
import { existsSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const HOST_DIR = dirname(fileURLToPath(import.meta.url))
const SHIM_ROOT = join(HOST_DIR, 'node_modules', '@deepseek-ai')

export async function resolve(specifier, context, nextResolve) {
  if (specifier.startsWith('@deepseek-ai/')) {
    const name = specifier.slice('@deepseek-ai/'.length)
    const dir = join(SHIM_ROOT, name)
    if (existsSync(join(dir, 'index.js'))) {
      return { url: pathToFileURL(join(dir, 'index.js')).href, shortCircuit: true, format: 'module' }
    }
  }
  return nextResolve(specifier, context)
}
