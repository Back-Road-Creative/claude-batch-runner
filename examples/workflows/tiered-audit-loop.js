// tiered-audit-loop — cheap multi-lens discovery until dry, strong-tier verify per finding.
//
// ---------------------------------------------------------------------------
// THIS FILE DOES NOT RUN STANDALONE. It is not Node, and `node
// tiered-audit-loop.js` will not work. It is a program for an agent-workflow
// host that evaluates the file with these globals already in scope:
//
//   args      the caller's arguments object
//   agent()   run one agent turn; returns a promise of its schema-validated output
//   parallel()  run an array of thunks concurrently
//   log()     append a line to the run log
//   budget    { total, remaining() } token accounting for the run
//
// It is included here as a worked design, not as a dependency. The same
// find-then-adversarially-verify shape is what claude_batch_runner expresses
// declaratively via `verify` + a rubric. Read it for the pattern; port the
// globals if you want to run it.
// ---------------------------------------------------------------------------

export const meta = {
  name: 'tiered-audit-loop',
  description: 'Loop-until-dry discovery with tiered verify: cheap multi-lens finder waves until 2 consecutive dry rounds; each fresh finding escalates to a strong-tier advisor before it is confirmed.',
  phases: [ { title: 'Find' }, { title: 'Verify' } ],
}

// One example environment. Add your own keys here — an environment is just the
// pair of model tiers you want the two stages to run on.
const CFG = {
  example: { finder:{model:'sonnet',effort:'low'}, advisor:{model:'opus', effort:'high'} },
}[args?.env]
if (!CFG) throw new Error("pass args:{env:'example', target:'<path/desc>'}")

const LENSES = ['correctness','silent-failure','completeness','security']  // each finder is blind to the others
const MAX_ROUNDS = 10  // hard backstop — the ONLY guard on a run with no token target set
const FIND_SCHEMA = { type:'object', additionalProperties:false, required:['findings'], properties:{
  findings:{ type:'array', items:{ type:'object', additionalProperties:false,
    required:['file','line','summary'],
    properties:{ file:{type:'string'}, line:{type:'integer'}, summary:{type:'string'} } } } } }
const VERDICT_SCHEMA = { type:'object', additionalProperties:false, required:['real','why'],
  properties:{ real:{type:'boolean'}, why:{type:'string'} } }

const key = f => `${f.file}:${f.line}`
const seen = new Set(), confirmed = []
let dry = 0, round = 0
while (dry < 2 && round < MAX_ROUNDS) {
  round++
  if (budget.total && budget.remaining() < 60_000) { log('budget floor hit — stopping'); break }
  // One CHEAP finder per lens, run as a barrier: the full round must land before
  // dedup, or two lenses report the same location as two findings. Vary the prompt
  // by round so later rounds hunt the tail instead of replaying round 1.
  const found = (await parallel(LENSES.map(lens => () =>
    agent(`Audit ${args.target} through the ${lens} lens (round ${round} — look for issues NOT already ` +
          `at these locations: ${[...seen].slice(0,100).join(', ') || 'none yet'}). ` +
          `Return {findings:[{file,line,summary}]}.`,
      { label:`find:${lens}:r${round}`, phase:'Find', schema:FIND_SCHEMA,
        model:CFG.finder.model, effort:CFG.finder.effort }))))
    .filter(Boolean).flatMap(r => r.findings)
  const fresh = found.filter(f => !seen.has(key(f)))            // dedup vs ALL seen, NOT vs confirmed
  if (!fresh.length) { dry++; log(`dry round ${dry}/2`); continue }
  dry = 0; fresh.forEach(f => seen.add(key(f)))
  // The advisor tries to REFUTE each fresh finding; only survivors are kept. Asking
  // for confirmation instead would confirm nearly everything.
  const judged = await parallel(fresh.map(f => () =>
    agent(`Adversarially verify this finding — try to REFUTE it. Default real=false if uncertain.\n` +
          `${f.file}:${f.line} — ${f.summary}`,
      { label:`verify:${key(f)}`, phase:'Verify', schema:VERDICT_SCHEMA,
        model:CFG.advisor.model, effort:CFG.advisor.effort }).then(v => ({ f, v }))))
  confirmed.push(...judged.filter(x => x && x.v?.real).map(x => x.f))
  log(`round ${round}: ${confirmed.length} confirmed so far`)
}
if (round >= MAX_ROUNDS) log(`round cap ${MAX_ROUNDS} hit before convergence — coverage may be incomplete`)
return confirmed
