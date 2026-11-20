// 直接调用「插件市场自己的」pnpm 探针，输出 JSON。
// 为什么不自己跑 pnpm --version：市场的探针还叠加了 host 包管理器那一层，
// 且失败原因（failure.kind / output）只有它自己能给——手工测出来的 "11.28.5" 不代表市场认为可用。
// 用法：node probe_pnpm.mjs
// 输出：{"ok":true,"failure":null}
import { pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';

const CLI = process.env.DSH_CLI_JS
  || 'D:/本地部署大肥鱼环境/dsh/profiles/desktop/node_modules/dshmarket/lib/dsh-cli.js';

let ok = false, failure = null, err = null;
try {
  const m = await import(pathToFileURL(CLI).href);
  ok = await m.probePnpm();
  if (!ok && typeof m.lastPnpmProbeFailure === 'function') {
    const f = m.lastPnpmProbeFailure();
    failure = f ? { kind: f.kind, output: String(f.output || '').slice(0, 400) } : null;
  }
} catch (e) {
  err = String(e && e.message || e).slice(0, 500);
}

let version = '';
try {
  version = execFileSync('pnpm', ['--version'], { shell: true, timeout: 20000 })
    .toString().trim().split('\n')[0].trim();
} catch (e) { version = ''; }

console.log(JSON.stringify({ ok, failure, error: err, pnpmVersion: version }, null, 2));
process.exit(ok ? 0 : 1);
