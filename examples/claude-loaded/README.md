# claude-loaded

Show which files are loaded into a Claude Code session's context, with a token
estimate for each one.

A long session gets expensive quietly. Every `CLAUDE.md` in the directory chain
and the project's auto-memory index are re-sent on *every* turn, and each file a
tool Read stays in the window for the rest of the session. This prints that set,
sorted largest first, so the thing bloating your context is a row you can see
rather than a number you can't explain.

It is one bash script with no Python in it. It does not import
`claude_batch_runner` — it is here because it belongs to the same toolbox, not
because it uses the library.

```bash
examples/claude-loaded/claude-loaded
```

```
Session: 4f9c2ab1  (/srv/project)

Files loaded into context:

  TOKENS  TYPE     PATH
  ------  ----     ----
  4.5K    static   ~/.claude/CLAUDE.md
  3.0K    static   /srv/project/CLAUDE.md
  1.6K    static   ~/.claude/projects/-srv-project/memory/MEMORY.md

Breakdown (sums to total):
  Files listed above (CLAUDE.md + memory):       9.1K
  Files Read by tools this session:             12.4K
  Everything else (system + tools + conv):      31.0K
                                             --------
  Total context:                                52.5K / 200.0K  (26.3%)
```

`static` rows are re-sent every turn. `read` rows entered the window once, when
a tool read them. "Everything else" is the remainder after subtracting the
listed files from the total the API actually reported — system prompt, tool
schemas, and the conversation itself.

## How it finds your session

With no arguments it walks up the process tree from its own parent, up to eight
levels, looking for a PID with a session file in `~/.claude/sessions/`. That
resolves to a session id, which resolves to a transcript under
`~/.claude/projects/`. So it works when you run it *from inside* a Claude Code
session, whose shell is a descendant of the `claude` process.

From outside one, name the session yourself:

```bash
claude-loaded --all                     # what's running
claude-loaded --session 4f9c2ab1        # by session-id prefix
claude-loaded --transcript path.jsonl   # by transcript path
```

If it can't resolve a session it prints the running-session list and exits `2`.

## Modes

| Flag | What it does |
|---|---|
| *(none)* | The breakdown above, for the current session. |
| `--all` | List running Claude Code sessions: PID, cwd, session id. |
| `--session <id>` | Use this session id (prefix match). |
| `--transcript <path>` | Use this transcript file directly. |
| `--prune` | Report auto-memory files that look abandoned. |
| `--budget [CHARS]` | Check the always-loaded set against a size ceiling. |
| `--cwd <dir>` | Which directory `--budget` measures. Default `$PWD`. |
| `--no-color` | No ANSI escapes. |
| `--json` | Machine-readable output; implies `--no-color`. |
| `--help` | Usage. |

### `--budget` — the one you can automate

`--budget` measures only the set that is loaded on every single turn: the global
`~/.claude/CLAUDE.md`, every `CLAUDE.md` from the given directory up toward `/`,
and that project's auto-memory `MEMORY.md` index. Under the ceiling it prints
nothing and exits `0`; over it, it lists the contributors largest-first and
exits `1`.

```bash
claude-loaded --budget            # default ceiling: 38000 chars
claude-loaded --budget 20000 --cwd /path/to/project
```

```
[static-budget] injected bundle is 41210 chars (budget 38000) for /path/to/project
[static-budget] contributors:
   16110  ~/.claude/CLAUDE.md
   14300  /path/to/project/CLAUDE.md
   10800  ~/.claude/projects/-path-to-project/memory/MEMORY.md
```

This mode reads no transcript and needs no session, which is what makes it
usable from a `SessionStart` hook — it warns you while consolidating is still
cheap, before the harness's own oversized-`CLAUDE.md` warning fires. The default
of 38000 sits just under that.

Note that it resolves the memory index by the **git toplevel** of the directory,
not the raw path, because that is how the harness keys auto-memory to a project.
A session in `repo/sub/dir` is injected `repo`'s index.

### `--prune` — abandoned auto-memory

Lists the topic files in the session project's memory directory with their age
and whether the filename has turned up in any recent transcript, then names the
ones that are both old and unreferenced.

```bash
claude-loaded --prune
STALE_DAYS=90 RECENT_DAYS=14 claude-loaded --prune
```

A file is a candidate when it has been unmodified for `STALE_DAYS` **and** its
name appears in no transcript modified within `RECENT_DAYS`. It only ever
prints; it deletes nothing, and it tells you the two commands to run yourself.
The "recently referenced" test is a filename grep across transcripts, so a
distinctive filename is judged well and a generic one (`notes.md`) is not.

## Environment

| Variable | Default | Effect |
|---|---|---|
| `CLAUDE_HOME` | `$HOME/.claude` | Where sessions, projects, and the global `CLAUDE.md` live. |
| `CLAUDE_CTX_MAX` | derived from the model | Context-window size used for the percentage. |
| `STALE_DAYS` | `60` | `--prune`: unmodified-for-N-days threshold. |
| `RECENT_DAYS` | `30` | `--prune`: unseen-in-transcripts-for-N-days threshold. |
| `PRUNE_HINT_MIN` | `10` | Topic-memory count above which the default view suggests `--prune`. |
| `COST_RATE_USD_M` | `1.50` | Read, but currently unused — see below. |

Without `CLAUDE_CTX_MAX` the window is inferred from the `model` in
`~/.claude/settings.json`: a `[1m]` alias gets 1,000,000, anything else 200,000.
An unrecognized model therefore reads as 200K, so set the variable explicitly if
that is wrong for yours.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Ran. Under budget, in `--budget` mode. |
| `1` | Over budget, or an unknown argument. |
| `2` | Could not resolve a session. |

## Requirements

`bash`, `jq`, `awk`, and Linux. Session auto-resolution reads `/proc`, and the
file-age logic uses GNU `stat -c`, so neither works as-is on macOS or BSD.
Nothing here spawns `claude`, spends anything, or writes any file.

## What it does not tell you accurately

- **Token counts for files are estimated at 3.6 characters per token**, not
  produced by a tokenizer. Treat them as relative sizes for spotting the big
  rows, not as billing figures. The one exact number on the screen is "Total
  context", which comes from the last API response's own usage.
- **The per-file number on `read` rows is not per-file.** The counting step
  never narrows the transcript to the file it is pricing, so every `read` row
  reports the same value — the total size of *all* tool output in the session —
  and their sum is correspondingly inflated. Which files were read is right;
  how big each one was is not. Static rows are measured on disk and are
  unaffected, as are `--budget` and `--prune`.
- **Only the `Read` tool counts.** Output from Grep, Glob, Bash, and everything
  else lands in "Everything else" rather than getting a row.
- **No cost figure is printed, despite `--help`.** The usage text advertises a
  "cost per turn" and a `COST_RATE_USD_M` rate to compute it with. The formatter
  exists in the script but nothing calls it, so setting the variable changes no
  output. Read the help line as a plan, not a feature.
- **`--all` prints `startedAt` as stored.** When the harness writes it as a
  number rather than a timestamp, that column shows the raw number.
