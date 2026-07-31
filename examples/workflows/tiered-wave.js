// tiered-wave — fan cheap workers over a work-list; escalate only the hard ones.
//
// ---------------------------------------------------------------------------
// THIS FILE DOES NOT RUN STANDALONE. It is not Node, and `node tiered-wave.js`
// will not work. It is a program for an agent-workflow host that evaluates the
// file with these globals already in scope:
//
//   args      the caller's arguments object
//   agent()   run one agent turn; returns a promise of its schema-validated output
//   parallel()  run an array of thunks concurrently
//   pipeline()  run an array of items through ordered stages, item by item
//   phase()   declare which phase subsequent work belongs to
//   log()     append a line to the run log
//   budget    { total, remaining() } token accounting for the run
//
// It is included here as a worked design, not as a dependency: the same tiered
// shape is what claude_batch_runner's spec expresses declaratively (a cheap
// `worker`, an `escalate.condition`, a stronger `advisor`). Read it for the
// pattern; port the globals if you want to run it.
// ---------------------------------------------------------------------------

export const meta = {
  name: 'tiered-wave',
  description: 'A modest-effort orchestrator fans cheap workers over a work-list; each worker escalates ONLY its hard or low-confidence unit to a strong-tier advisor, so most tokens bill at the executor rate.',
  phases: [ { title: 'Plan' }, { title: 'Execute' }, { title: 'Advise' } ],
}

// One example environment. Add your own keys here — an environment names the
// worker agent, the model tiers, and how a finished unit is delivered.
const CFG = {
  example: {
    worker:  { agentType: 'example-pr-author', model: 'sonnet', effort: 'medium', isolation: 'worktree' },
    advisor: { model: 'opus', effort: 'high' },   // correctness / silent-failure judge on the diff
    base: 'origin/main',
    delivery: 'Open the PR yourself with gh pr create --base main (NEVER merge). Return the PR number/URL in the pr field.',
  },
}[args?.env]
if (!CFG) throw new Error("pass args:{env:'example', task:'<wave description>', items?:[...], tag?:'<run tag>'}")
const TAG = args.tag ?? 'tw'

const PLAN_SCHEMA = { type:'object', additionalProperties:false, required:['units'], properties:{
  units:{ type:'array', items:{ type:'object', additionalProperties:false,
    required:['id','title','file','scope'],
    properties:{ id:{type:'string'}, title:{type:'string'}, file:{type:'string'}, scope:{type:'string'} } } } } }
const WORKER_SCHEMA = { type:'object', additionalProperties:false,
  required:['id','summary','confidence','highStakes'],
  properties:{ id:{type:'string'}, summary:{type:'string'}, pr:{type:'string'},
    confidence:{type:'number'}, highStakes:{type:'boolean'} } }
const ADVISOR_SCHEMA = { type:'object', additionalProperties:false,
  required:['id','verdict','notes'],
  properties:{ id:{type:'string'}, verdict:{type:'string',enum:['ship','revise','block']}, notes:{type:'string'} } }

phase('Plan')
// The orchestrator runs at MODEST effort on purpose. Planning is the stage where
// maximum effort buys the least: it produces a longer deliberation about a list.
if (!args.items && !args.task) throw new Error('pass args.task (what to decompose) or args.items (pre-made unit list)')
const units = args.items ?? (await agent(
  `Wave to decompose: ${args.task}\n` +
  `Decompose it into INDEPENDENT, single-scope units (no import edge between them). ` +
  `Each becomes its own small PR. Return {units:[{id,title,file,scope}]}.`,
  { label: 'plan', schema: PLAN_SCHEMA, effort: 'medium' },
)).units

const results = await pipeline(
  units,
  // Stage 1 — cheap EXECUTOR authors the change test-first. Its cwd IS its isolated
  // worktree; the prompt supplies the branch, base, and title the agent contract
  // requires (a well-written worker agent STOPs if any of them is missing).
  (u) => agent(
    `Unit ${u.id}: ${u.title}\nFile: ${u.file}\nScope: ${u.scope}\n` +
    `WORKTREE: your current working directory — it is a fresh isolated git worktree of this repo. ` +
    `Work ONLY there.\n` +
    `BRANCH: create and use ${TAG}/${u.id} off ${CFG.base}.\n` +
    `COMMIT/PR TITLE: ${u.title}\n` +
    `Author the single-scope change test-first, run this repo's local CI, respect its diff cap. ` +
    `${CFG.delivery}\n` +
    `Set confidence<0.8 or highStakes=true if it touches a cross-cutting invariant, a schema, ` +
    `or you are unsure it is correct.`,
    { label:`exec:${u.id}`, phase:'Execute', schema:WORKER_SCHEMA,
      agentType:CFG.worker.agentType, model:CFG.worker.model,
      effort:CFG.worker.effort, isolation:CFG.worker.isolation }),
  // Stage 2 — escalate to the ADVISOR only when flagged. Most units skip it, so
  // strong-tier spend stays confined to the hard minority. That is the whole
  // economic argument for this shape. The advisor reads the REAL diff, never the
  // executor's summary of it — a wrong change usually comes with a confident summary.
  (r, u) => (r && (r.confidence < 0.8 || r.highStakes))
    ? agent(
        `Advisor review of unit ${u.id} (${u.title}). Executor was unsure or flagged high-stakes.\n` +
        `PR/branch: ${r.pr ?? `${TAG}/${u.id}`} (base ${CFG.base}).\n` +
        `READ THE ACTUAL DIFF (gh pr diff <n>, or git fetch && git diff ${CFG.base}...<branch>) — ` +
        `do not judge from the summary alone.\nExecutor summary: ${r.summary}\n` +
        `Judge correctness + hidden-invariant breakage; verdict ship|revise|block.`,
        { label:`advise:${u.id}`, phase:'Advise', schema:ADVISOR_SCHEMA,
          model:CFG.advisor.model, effort:CFG.advisor.effort }).then(a => ({ ...r, advisor:a }))
    : r,
)
const ok = results.filter(Boolean)
const dropped = units.length - ok.length
if (dropped) log(`WARNING: ${dropped}/${units.length} unit(s) failed or were skipped — list them in the wrap-up`)
return { results: ok, dropped }
