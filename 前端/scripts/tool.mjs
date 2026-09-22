import { spawn } from 'node:child_process'
import { mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { resolve } from 'node:path'

// 所有子进程强制以本模块为工作目录；临时目录、npm和浏览器缓存不污染用户home。
const root = fileURLToPath(new URL('../', import.meta.url))
const cache = resolve(root, '../.cache/frontend')
mkdirSync(resolve(cache, 'tmp'), { recursive: true })
const env = { ...process.env, TEMP: resolve(cache, 'tmp'), TMP: resolve(cache, 'tmp'),
  npm_config_cache: resolve(cache, 'npm'), npm_config_userconfig: resolve(root, '.npmrc'), PLAYWRIGHT_BROWSERS_PATH: resolve(cache, 'ms-playwright') }
const [name, ...args] = process.argv.slice(2)
const bins = { vite: 'vite/bin/vite.js', vitest: 'vitest/vitest.mjs',
  playwright: '@playwright/test/cli.js', typecheck: 'vue-tsc/bin/vue-tsc.js' }
if (name === 'npm') {
  const executable = process.platform === 'win32' ? 'npm.cmd' : 'npm'
  const child = spawn(executable, ['--prefix', root, ...args], { cwd: root, env, stdio: 'inherit', shell: process.platform === 'win32' })
  child.on('exit', code => process.exit(code ?? 1))
} else if (name === 'build') {
  const check = spawn(process.execPath, [resolve(root, 'node_modules', bins.typecheck), '--noEmit'], { cwd: root, env, stdio: 'inherit' })
  check.on('exit', code => {
    if (code !== 0) process.exit(code ?? 1)
    const build = spawn(process.execPath, [resolve(root, 'node_modules', bins.vite), 'build'], { cwd: root, env, stdio: 'inherit' })
    build.on('exit', result => process.exit(result ?? 1))
  })
} else if (name && bins[name]) {
  const child = spawn(process.execPath, [resolve(root, 'node_modules', bins[name]), ...args], { cwd: root, env, stdio: 'inherit' })
  child.on('exit', code => process.exit(code ?? 1))
} else {
  console.error('用法: node scripts/tool.mjs npm|vite|vitest|playwright|typecheck|build [参数]')
  process.exit(1)
}
