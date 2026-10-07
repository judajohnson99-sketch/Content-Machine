import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// The design system is only a system if the modules cannot opt out of it.
// Before these tests existed, index.css claimed "nothing hard-codes a colour"
// while 76 literals across 15 of 16 modules said otherwise, and a typo'd
// `var(--danger, #f87171)` rendered its fallback forever because `--danger`
// was never a token. Both classes of drift are now failures, not comments.
//
// Read from disk rather than importing: vitest stubs CSS imports (test.css
// defaults to false), so `?raw` on a stylesheet yields an empty module.
// vitest runs with the frontend package root as cwd (vite.config.ts).
const SRC = join(process.cwd(), 'src')

function cssModules(dir: string): [string, string][] {
  const found: [string, string][] = []
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name)
    if (entry.isDirectory()) found.push(...cssModules(path))
    else if (entry.name.endsWith('.module.css')) found.push([path, readFileSync(path, 'utf8')])
  }
  return found
}

const MODULES = cssModules(SRC)
const globalCss = readFileSync(join(SRC, 'index.css'), 'utf8')

/** Colour literals: hex of any length, plus rgb()/rgba(). */
const COLOUR_LITERAL = /#[0-9a-fA-F]{3,8}\b|\brgba?\(/

/** Token definitions, e.g. `--accent-bg-hover:`. */
function definedTokens(css: string): Set<string> {
  return new Set(Array.from(css.matchAll(/^\s*(--[a-z0-9-]+)\s*:/gm), (m) => m[1]))
}

/** Token references, e.g. `var(--accent-bg-hover)` or `var(--x, fallback)`. */
function referencedTokens(css: string): string[] {
  return Array.from(css.matchAll(/var\(\s*(--[a-z0-9-]+)/g), (m) => m[1])
}

describe('design tokens', () => {
  it('finds the CSS modules it is meant to guard', () => {
    expect(MODULES.length).toBeGreaterThan(10)
  })

  it.each(MODULES)('%s hard-codes no colour', (_path, css) => {
    const offenders = css
      .split('\n')
      .map((line, index) => `${index + 1}: ${line.trim()}`)
      .filter((line) => COLOUR_LITERAL.test(line))
    expect(offenders).toEqual([])
  })

  it('references only tokens index.css defines', () => {
    const defined = definedTokens(globalCss)
    const unknown: Record<string, string[]> = {}
    for (const [path, css] of MODULES) {
      const local = definedTokens(css)
      for (const token of referencedTokens(css)) {
        if (defined.has(token) || local.has(token)) continue
        unknown[token] = [...(unknown[token] ?? []), path]
      }
    }
    expect(unknown).toEqual({})
  })

  it('defines a hover tint for every status tone', () => {
    const defined = definedTokens(globalCss)
    for (const tone of ['success', 'warning', 'danger', 'info', 'neutral']) {
      for (const slot of ['fg', 'bg', 'border', 'bg-hover']) {
        expect(defined).toContain(`--${tone}-${slot}`)
      }
    }
  })

  it('defines the material, type and stacking scales the shell relies on', () => {
    const defined = definedTokens(globalCss)
    const required = [
      '--bg-well', '--glass-1', '--glass-2', '--glass-3',
      '--blur-sm', '--blur-md', '--blur-lg',
      '--sheen', '--sheen-subtle', '--sheen-strong',
      '--grad-surface', '--grad-accent', '--grad-accent-hover',
      '--leading-tight', '--leading-normal', '--leading-relaxed',
      '--weight-regular', '--weight-medium', '--weight-semibold',
      '--tracking-tight', '--tracking-normal', '--tracking-wide',
      '--z-base', '--z-sticky', '--z-dropdown', '--z-overlay', '--z-modal', '--z-toast',
    ]
    for (const token of required) expect(defined).toContain(token)
  })
})
