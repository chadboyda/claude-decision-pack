import type { On } from 'claude-code'

/**
 * decision-pack's mod half. It shows the open decisions under the prompt
 * ("Decisions: 3 open · next due 5:00 PM EST · 1 not closed") and answers `/decision-pack`.
 * The status line is UI only: the model never sees it. What the model sees at session start, and the
 * Stop check for chat questions, are plain settings.json command hooks (`decision-pack hook
 * session-start`, `decision-pack hook stop`; README "Hooks").
 *
 * `/decision-pack due` only reports what is past due: defaults apply inside `sync-plan --apply`,
 * after the user's page answers are read, never on their own.
 */
const REFRESH_MS = 60_000

async function dp($: any, args: string[]): Promise<{ ok: boolean; out: string }> {
  try {
    const r = await $.process.run(['python3', '-I', `${$.plugin.root}/lib/dp.py`, ...args], { timeoutMs: 20_000 })
    return { ok: r.exitCode === 0, out: (r.exitCode === 0 ? r.stdout : r.stderr || r.stdout).trim() }
  } catch (err) {
    return { ok: false, out: `could not run python3 (${err instanceof Error ? err.message : String(err)})` }
  }
}

async function refresh($: any): Promise<void> {
  const r = await dp($, ['status', '--line'])
  $.ui.status(r.ok ? r.out.replace(/^Decisions: /, '') : 'status failed')
}

export function register(on: On) {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'decision-pack',
      description: 'Decision packs: list them, or `sync <project>` for the read-back steps',
      argumentHint: '[status|sync <project>|due|local <project>]',
      immediate: true,
    })
    $.clock.every(REFRESH_MS, () => {
      void refresh($)
    })
    void refresh($)
    return next(e)
  })

  on('command.run', { command: 'decision-pack' }, async ($, e) => {
    const parts = e.args.trim().split(/\s+/).filter(Boolean)
    const sub = (parts[0] || 'status').toLowerCase()
    let r
    if (sub === 'status') r = await dp($, ['status'])
    else if (sub === 'sync' && parts[1]) r = await dp($, ['sync-plan', parts[1]])
    else if (sub === 'due') r = await dp($, ['due'])
    else if (sub === 'local' && parts[1]) r = await dp($, ['local', parts[1]])
    else return { text: 'Usage: /decision-pack [status | sync <project> | due | local <project>]' }
    await refresh($)
    return { text: r.ok ? r.out : `${sub} failed: ${r.out}` }
  })
}
