# Coding-agent worker backend, and code review as a canned workflow

## Context

orxtra's worker system executes tasks on registered workers with typed
capabilities, under the runtime's law: every tool call inside an active task,
pre/post-checks as verification boundaries, USD budgets, full tracing, durable
principal identity. Two independent considerations extend that law to coding
work. No pressure on either.

## 1. Coding-agent worker backend

Problem: orxtra deliberately has no bash tool and no free-form coding loop —
correct for its own agents, but it means substantial code-editing work
(multi-file changes, build-test-fix cycles) has no natural execution target.
Rebuilding a competitive coding loop in-house would be a huge, ever-moving
target.

Direction: a worker/execution-target kind that drives an interactive
coding-agent CLI as a subprocess. Modern coding agents (Claude Code being the
canonical example) expose a streaming JSON mode over pipes
(`--input-format stream-json --output-format stream-json`) with typed events,
permission-prompt interception, session resumption, and per-turn cost
reporting — enough surface for a worker to run one coding task per orxtra
task while the runtime's law still applies from outside: the task boundary
scopes the work, post-checks verify the diff/tests, the budget consumes the
stream's reported cost, and every event lands in trace attributed to the
task's principal.

Pros: coding capability of a frontier agent without rebuilding the loop;
verification stays structural (checks judge the output, not the agent's
self-report). Cons/risks: the external agent's own tool use is only as
constrained as its permission mechanisms allow — the integration must map
orxtra's doctrine onto the CLI's permission-prompt interception,
deny-by-default, and treat the agent's filesystem effects as untrusted until
checks pass. Auth/credential handling for the subprocess is its own design
question (the CLI has its own login state; orxtra must not inherit ambient
credentials silently).

Affected: `protocols/` (a new execution-target/capability vocabulary),
`worker/` (a new worker kind alongside native/docker), `scheduler/`
(dispatch), `session/` (cost accounting from external streams), trace.
Effort: large.

## 2. Code review as a first-class canned workflow

Problem: code review is the most recurring verification-heavy task shape, and
orxtra's primitives — fan-out, read-only verdict agents as post-checks,
retry/escalation — are exactly its shape, but no shipped workflow proves it.

Direction: a canned review workflow: finder agents fan out per dimension
(correctness, security, performance, tests), each finding then faces verdict
agents prompted to refute it, with a refutation-vote threshold deciding
survival; the artifact is a typed findings list (file, line, summary,
failure scenario, verdict) persisted through trace. Serves double duty as a
product feature and as the conformance example for the verification
primitives.

Affected: a workflow definition + prompt fragments (`scheduler/prompts/` or a
new examples/product home), a findings type in `protocols/`, docs.
Effort: medium.
