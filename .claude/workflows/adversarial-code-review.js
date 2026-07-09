/**
 * adversarial-code-review — multi-dimension review of the branch diff where every
 * finding must survive an adversarial refutation panel before it is reported.
 *
 * Shape: Scope (one agent resolves the diff) → Review (one reviewer per
 * dimension, in parallel) → Verify (each finding is attacked by an independent
 * skeptic panel prompted to REFUTE it) → Synthesize (dedup + rank the survivors
 * into a report). The Review and Verify stages run as a pipeline, so a
 * dimension's findings start being refuted the moment that dimension finishes —
 * no barrier waiting on the slowest reviewer.
 *
 * The point of the adversarial pass: a single reviewer emits plausible-but-wrong
 * findings (misread control flow, a "bug" the tests already cover, a convention
 * it thinks is violated but isn't). Making each finding earn its place against
 * skeptics whose job is to kill it strips those out, so what reaches the user is
 * high-signal.
 *
 * Usage (run via the Workflow tool):
 *   Workflow({ name: 'adversarial-code-review' })
 *   Workflow({ name: 'adversarial-code-review', args: { base: 'origin/main' } })
 *   Workflow({ name: 'adversarial-code-review', args: { base: 'HEAD~3', votes: 5 } })
 *
 * args (all optional):
 *   base   — git ref to diff against (default 'origin/main'). The review covers
 *            `git diff <base>...HEAD` plus any uncommitted working-tree changes.
 *   votes  — skeptics per finding (default 3; a finding dies on a majority-refute).
 *   paths  — array of path prefixes to restrict the review to (default: whole diff).
 */

export const meta = {
  name: 'adversarial-code-review',
  description:
    'Review the branch diff across correctness, invariants, Django/ORM, convention drift and tests — then adversarially refute every finding so only real ones are reported',
  whenToUse:
    'Before opening or merging a PR on this repo. Fans out one reviewer per dimension over the branch diff, then makes each finding survive a refutation panel. Higher signal than a single-pass review; costs more tokens.',
  phases: [
    { title: 'Scope', detail: 'resolve the diff and changed files' },
    { title: 'Review', detail: 'one reviewer per dimension, in parallel' },
    { title: 'Verify', detail: 'refutation panel attacks each finding' },
    { title: 'Synthesize', detail: 'dedup and rank the survivors' },
  ],
}

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const BASE = (args && args.base) || 'origin/main'
const VOTES = (args && Number(args.votes)) || 3
const PATHS = (args && Array.isArray(args.paths) && args.paths.length && args.paths) || null
const PATH_NOTE = PATHS
  ? `\nRestrict your review to files under these paths only: ${PATHS.join(', ')}.`
  : ''

// The dimensions each get their own reviewer. Kept aligned with the nine
// CLAUDE.md invariants and the project conventions the `reviewer` agent knows.
const DIMENSIONS = [
  {
    key: 'correctness',
    focus:
      'Logic bugs and correctness. Wrong conditionals, off-by-one, unhandled ' +
      'None/empty, mishandled edge cases, and especially incorrect Match / ' +
      'Registration state-machine transitions (PROPOSED → PENDING → ACCEPTED / ' +
      'DECLINED / EXPIRED / CANCELLED, and the re-queue / priority / PAUSED / ' +
      'SUSPENDED side effects). Concurrency and race conditions in the matching ' +
      'engine (missing select_for_update / transaction boundaries).',
  },
  {
    key: 'invariants-security',
    focus:
      'The nine CLAUDE.md invariants and security. In particular: contact PII ' +
      '(email + phone) must stay hidden until BOTH parties accept — only first ' +
      'name/initials may show before that; matches only ever proposed between an ' +
      'eligible pair; 1:1 non-terminal match per season; no mark_safe() on ' +
      'user-supplied content; emails lowercased at every entry point; signed-link ' +
      'tokens single-purpose + expiring with distinct salts; HTMX partial views ' +
      'guarded by require_htmx (400 on plain HTTP); no secrets in source. Also ' +
      'generic auth/authorisation gaps, IDOR on signed links, and injection.',
  },
  {
    key: 'django-orm',
    focus:
      'Django 6.0 / ORM correctness and performance. N+1 queries (missing ' +
      'select_related / prefetch_related), queries in loops, missing indexes, ' +
      'transaction.atomic / on_commit misuse, migration safety, and the project ' +
      'ban on Django signals for side effects (side effects must be called inline ' +
      'from a service function, never via post_save).',
  },
  {
    key: 'convention-drift',
    focus:
      'Ambassadeurs conventions. Every concrete model ships the full kit ' +
      '(BaseModel, admin class, to_string(), Meta.ordering, custom queryset, ' +
      'factory + tests). Fixed choices are UPPER_CASE TextChoices on the model. ' +
      'Derived, argument-free, side-effect-free predicates are @property, not ' +
      'methods. Eligibility/assignment logic lives in matching/ services, not in ' +
      'views. British English in code/comments/docs. All user-facing copy wrapped ' +
      'for translation (gettext / {% translate %}). Module header comments + ' +
      'docstrings; typed arguments.',
  },
  {
    key: 'tests',
    focus:
      'Test coverage and test correctness. New code must have covering tests ' +
      '(90% target). Factories called with .create() (never bare instantiation); ' +
      'all datetimes carry tzinfo. Missing edge-case coverage, tests that assert ' +
      'nothing meaningful, or behaviour changes with no matching test update.',
  },
]

const FINDINGS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['file', 'line', 'severity', 'summary', 'failure_scenario', 'evidence'],
        properties: {
          file: { type: 'string', description: 'Repo-relative path' },
          line: { type: 'integer', description: '1-indexed line the finding anchors to' },
          severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
          summary: { type: 'string', description: 'One sentence: what is wrong' },
          failure_scenario: {
            type: 'string',
            description: 'Concrete inputs/state → wrong output/crash/leak',
          },
          evidence: {
            type: 'string',
            description: 'The exact code (quoted) or convention/invariant that grounds the claim',
          },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['refuted', 'confidence', 'reasoning'],
  properties: {
    refuted: {
      type: 'boolean',
      description: 'true if the finding is wrong, already handled, or not actually a defect',
    },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    reasoning: { type: 'string', description: 'Why it stands or falls, citing what you checked' },
  },
}

const REPORT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['report', 'confirmed'],
  properties: {
    report: { type: 'string', description: 'Markdown report, findings ranked most-severe first' },
    confirmed: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['file', 'line', 'severity', 'summary'],
        properties: {
          file: { type: 'string' },
          line: { type: 'integer' },
          severity: { type: 'string' },
          summary: { type: 'string' },
        },
      },
    },
  },
}

// The distinct lenses a refutation panel uses. Diversity beats N identical
// skeptics — each catches a failure mode the others miss. If VOTES exceeds the
// list, later skeptics cycle back through it (kept distinct by index in-prompt).
const LENSES = [
  'reproduce: try to actually trigger the failure scenario by tracing the real control flow. Refute if you cannot make it happen.',
  'already-handled: check whether existing code, a guard, a validator, a DB constraint, or a test already prevents this. Refute if it does.',
  'convention-check: verify the cited invariant/convention actually says what the finding claims, and that this code truly violates it (not a look-alike). Refute if the rule does not apply here.',
  'false-positive: assume the finding is wrong and argue that case as hard as you can — misread scope, wrong file, stale line, a Django/Python 3.14 idiom that only looks wrong.',
]

const key = (f) => `${f.file}:${f.line}:${f.severity}`

// ---------------------------------------------------------------------------
// 1. Scope — resolve the diff once so every reviewer works from the same base.
// ---------------------------------------------------------------------------

phase('Scope')

const scope = await agent(
  `Resolve the review scope for an adversarial code review of this repository.

Run these and report the result:
  git fetch origin --quiet || true
  git diff --stat ${BASE}...HEAD
  git diff --stat            # uncommitted working-tree changes, if any
  git diff --name-only ${BASE}...HEAD

The review target is the union of committed changes since ${BASE} and any
uncommitted working-tree changes.${PATH_NOTE}

Return the diff base you used, the list of changed files (repo-relative), and
whether the target diff is empty.`,
  {
    label: 'scope:diff',
    phase: 'Scope',
    agentType: 'general-purpose',
    schema: {
      type: 'object',
      additionalProperties: false,
      required: ['base', 'files', 'empty'],
      properties: {
        base: { type: 'string' },
        files: { type: 'array', items: { type: 'string' } },
        empty: { type: 'boolean' },
      },
    },
  },
)

if (!scope || scope.empty || !scope.files || scope.files.length === 0) {
  log(`No changes to review against ${BASE}. Nothing to do.`)
  return { base: BASE, confirmed: [], report: `No diff to review against \`${BASE}\`.` }
}

log(`Reviewing ${scope.files.length} changed file(s) across ${DIMENSIONS.length} dimensions.`)

// ---------------------------------------------------------------------------
// 2 + 3. Review → Verify, as a pipeline (no barrier between the two stages).
//   Stage 1: one reviewer per dimension emits findings.
//   Stage 2: each finding is attacked by a VOTES-strong refutation panel and
//            kept only if it survives a majority (i.e. NOT refuted by a majority).
// ---------------------------------------------------------------------------

const reviewPrompt = (d) =>
  `You are reviewing the diff \`git diff ${scope.base}...HEAD\` (plus uncommitted
working-tree changes) in this repository. Read the diff and the surrounding code
you need for context.${PATH_NOTE}

Review ONLY this dimension — other agents cover the rest, do not stray:

${d.focus}

Report only genuine defects in the CHANGED code (or code the change breaks). Do
not report style nits (ruff owns those), pre-existing issues unrelated to the
diff, or speculative "could be nicer" remarks. For each finding give the exact
file + line, a concrete failure scenario, and the quoted code or the specific
invariant/convention it violates. If the dimension is clean, return an empty
findings list.`

const verifyFinding = (finding, dimKey) =>
  parallel(
    Array.from({ length: VOTES }, (_v, i) => () =>
      agent(
        `Adversarially verify a code-review finding. Your DEFAULT is to refute:
only let it stand if you can confirm it is a real, currently-exploitable defect
in the changed code. If you are uncertain, refute it.

Lens for this pass — ${LENSES[i % LENSES.length]}

Finding (dimension: ${dimKey}):
  file: ${finding.file}:${finding.line}
  severity: ${finding.severity}
  summary: ${finding.summary}
  failure scenario: ${finding.failure_scenario}
  evidence claimed: ${finding.evidence}

Read the actual file at that location and the code around it. Trace the real
behaviour. Decide whether the finding survives.`,
        {
          label: `verify:${finding.file.split('/').pop()}:${finding.line}#${i + 1}`,
          phase: 'Verify',
          agentType: 'reviewer',
          schema: VERDICT_SCHEMA,
        },
      ),
    ),
  ).then((verdicts) => {
    const valid = verdicts.filter(Boolean)
    const refutes = valid.filter((v) => v.refuted).length
    // Survives only if refuters are NOT a majority. Ties / all-dead panels die.
    const survives = valid.length > 0 && refutes < Math.ceil(valid.length / 2)
    return { ...finding, dimension: dimKey, survives, refutes, votes: valid.length, verdicts: valid }
  })

phase('Review')

const perDimension = await pipeline(
  DIMENSIONS,
  (d) =>
    agent(reviewPrompt(d), {
      label: `review:${d.key}`,
      phase: 'Review',
      agentType: 'reviewer',
      schema: FINDINGS_SCHEMA,
    }),
  (review, d) => {
    const findings = (review && review.findings) || []
    if (findings.length === 0) return []
    return parallel(findings.map((f) => () => verifyFinding(f, d.key)))
  },
)

const allFindings = perDimension.filter(Boolean).flat().filter(Boolean)
const survivors = allFindings.filter((f) => f.survives)

log(
  `${allFindings.length} finding(s) raised; ${survivors.length} survived the refutation panel ` +
    `(${allFindings.length - survivors.length} refuted).`,
)

if (survivors.length === 0) {
  return {
    base: scope.base,
    raised: allFindings.length,
    confirmed: [],
    report:
      `# Adversarial code review\n\nBase: \`${scope.base}\`\n\n` +
      `Reviewed ${scope.files.length} changed file(s) across ${DIMENSIONS.length} dimensions. ` +
      `${allFindings.length} candidate finding(s) were raised and every one was refuted by ` +
      `its skeptic panel. **No confirmed issues.**`,
  }
}

// ---------------------------------------------------------------------------
// 4. Synthesize — dedup overlapping findings and rank the survivors.
// ---------------------------------------------------------------------------

phase('Synthesize')

const synthInput = survivors.map((f) => ({
  file: f.file,
  line: f.line,
  severity: f.severity,
  dimension: f.dimension,
  summary: f.summary,
  failure_scenario: f.failure_scenario,
  evidence: f.evidence,
  panel: `${f.votes - f.refutes}/${f.votes} skeptics upheld it`,
}))

const synthesis = await agent(
  `You are assembling the final report for an adversarial code review of this
repository (diff base \`${scope.base}\`). Below are the findings that SURVIVED an
adversarial refutation panel — each was attacked by independent skeptics and held
up. Do not re-litigate them.

Your job:
1. Merge duplicates — several dimensions may have flagged the same defect at the
   same location. Collapse them into one entry.
2. Rank most-severe first (critical → high → medium → low), and within a severity
   put the most certain first.
3. Write a concise markdown report. Start with a one-line summary (counts by
   severity), then one section per finding: heading \`### [severity] file:line\`,
   the defect, the concrete failure scenario, the panel result, and a suggested
   fix direction (one or two sentences — do not write the patch).

Surviving findings (JSON):
${JSON.stringify(synthInput, null, 2)}`,
  {
    label: 'synthesize:report',
    phase: 'Synthesize',
    agentType: 'reviewer',
    schema: REPORT_SCHEMA,
  },
)

return {
  base: scope.base,
  filesReviewed: scope.files.length,
  raised: allFindings.length,
  refuted: allFindings.length - survivors.length,
  confirmed: (synthesis && synthesis.confirmed) || synthInput,
  report: (synthesis && synthesis.report) || 'Synthesis agent returned no report; see confirmed[].',
}
