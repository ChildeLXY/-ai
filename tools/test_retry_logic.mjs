/**
 * 值班轮"整轮重试"逻辑测试（工具级，不打 B 站接口）。
 *
 * 跑法：
 *   node tools/test_retry_logic.mjs
 *
 * 为什么这样测：真跑一轮会拉 B 站信息流、可能发评论，不能拿来当试验田；
 * 所以把重试判定抽成纯函数 shouldRetryDuty 并导出，这里直接喂各种失败形态。
 */
const PLUGIN = 'file:///D:/本地部署大肥鱼环境/dsh/profiles/desktop/node_modules/dsh-bili-pond/index.js'

const mod = await import(PLUGIN)
const { shouldRetryDuty, DUTY_RETRY_MAX, DUTY_RETRY_DELAYS } = mod

let pass = 0
const fails = []
const ok = (name, cond, extra = '') => {
  if (cond) { pass += 1; console.log('  ✅ ' + name + (extra ? '  ' + extra : '')) }
  else { fails.push(name); console.log('  ❌ ' + name + (extra ? '  ' + extra : '')) }
}

console.log('常量：MAX=' + DUTY_RETRY_MAX + '  DELAYS=' + JSON.stringify(DUTY_RETRY_DELAYS))

// 1) 一轮真成功 → 不重试
{
  const p = shouldRetryDuty(null, 0)
  ok('成功轮不重试', p.retry === false && p.reason === 'success')
}

// 2) 可重试的普通失败（子进程 code=1，正是 glm-4.6v-flash 41 秒那次形态）
{
  const p = shouldRetryDuty({ code: 1, hint: '子进程异常退出（code=1）' }, 0)
  ok('code=1 首次失败 → 30 秒后重试', p.retry === true && p.attempt === 1 && p.delay === 30_000, 'delay=' + p.delay)
}

// 3) 第二次、第三次的退避
{
  const p2 = shouldRetryDuty({ code: 1 }, 1)
  const p3 = shouldRetryDuty({ code: 1 }, 2)
  ok('第二次失败 → 60 秒', p2.retry === true && p2.attempt === 2 && p2.delay === 60_000, 'delay=' + p2.delay)
  ok('第三次失败 → 120 秒', p3.retry === true && p3.attempt === 3 && p3.delay === 120_000, 'delay=' + p3.delay)
}

// 4) 次数用完 → 不再重试，交给下一轮定时任务
{
  const p = shouldRetryDuty({ code: 1 }, DUTY_RETRY_MAX)
  ok('重试 ' + DUTY_RETRY_MAX + ' 次后 → exhausted', p.retry === false && p.reason === 'exhausted')
}

// 5) 不该重试的三类
{
  ok('登录失效不重试', shouldRetryDuty({ code: 'login' }, 0).reason === 'fatal')
  ok('模型/线路配错不重试', shouldRetryDuty({ code: 'model' }, 0).reason === 'fatal')
  ok('子进程没起来不重试', shouldRetryDuty({ code: 'spawn_failed' }, 0).reason === 'spawn')
}

// 6) 空跑 / 太快结束 / 网络类失败 → 都该重试
{
  const codes = ['too_fast', 'timeout', 'network', undefined]
  for (const c of codes) {
    const p = shouldRetryDuty({ code: c }, 0)
    ok('code=' + String(c) + ' 可重试', p.retry === true, p.reason)
  }
}

// 7) 决策不会原地打转：原始这轮 + 最多 3 次重试后必然停
{
  let attempt = 0, rounds = 0
  while (shouldRetryDuty({ code: 1 }, attempt).retry) { attempt += 1; rounds += 1 }
  const after = shouldRetryDuty({ code: 1 }, attempt)
  ok('原始一轮 + 最多 ' + DUTY_RETRY_MAX + ' 次重试后停下', rounds === DUTY_RETRY_MAX && after.reason === 'exhausted', '重试次数=' + rounds)
}

// 8) 接线检查：源码里确实用上了这个纯函数（防止有人回退成旧的内联判断）
{
  const fs = await import('node:fs')
  const src = fs.readFileSync('D:/本地部署大肥鱼环境/dsh/profiles/desktop/node_modules/dsh-bili-pond/index.js', 'utf8')
  ok('handler 调用 shouldRetryDuty', src.includes('const retryPlan = shouldRetryDuty(fail, dutyRetryAttempt)'))
  ok('旧的内联 retryable 判断已移除', !src.includes('const retryable = !['))
  ok('重试前清理占位报告', src.includes('这一轮没写出报告') && src.includes('rmSync(f, { force: true })'))
}

console.log('\n===== 结果：' + pass + ' 通过 / ' + fails.length + ' 失败 =====')
if (fails.length) { console.log('失败项：' + fails.join('；')); process.exit(1) }
