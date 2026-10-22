import { describe, expect, it } from 'vitest'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { resolve } from 'node:path'

function files(dir: string): string[] { return readdirSync(dir).flatMap(name => { const p = resolve(dir, name); return statSync(p).isDirectory() ? files(p) : [p] }) }
describe('frontend disclosure and transport boundaries', () => {
  it('ships no database, raw log, dotenv, or model SDK', () => {
    const publicFiles = files('public')
    expect(publicFiles.some(p => /\.(sqlite|db|jsonl|env)$/.test(p))).toBe(false)
    const pkg = JSON.parse(readFileSync('package.json', 'utf8'))
    expect(Object.keys(pkg.dependencies)).not.toContain('openai')
    expect(Object.keys(pkg.dependencies).some(k => /react/i.test(k))).toBe(false)
  })
  it('application code contains no remote model URL, fetch, SQL execution or unsafe HTML', () => {
    for (const file of files('src').filter(p => /\.(ts|vue)$/.test(p))) {
      const text = readFileSync(file, 'utf8')
      expect(text, file).not.toMatch(/gw\.lunaroute|\/chat\/completions|Bearer\s|v-html|\bfetch\s*\(|sqlite3|DEEPSEEK_API_KEY|OPENAI_API_KEY/)
    }
  })
  it('does not expose parent environment through Vite and restricts connections', () => {
    expect(readFileSync('vite.config.ts', 'utf8')).toContain("envDir: './.offline-no-env'")
    expect(readFileSync('index.html', 'utf8')).toContain("connect-src 'self' ws://127.0.0.1:*")
  })
})
