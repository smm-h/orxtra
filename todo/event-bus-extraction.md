# Extract the event system into a standalone event-bus tool

Extract orxtra's unified central event system — the PG-backed event store,
subscriptions with filter predicates and action chains, accumulator
buffering, HTTP/webhook/SSE ingestion, notification delivery, and minimal
identity — into a separate tool that orxtra consumes as a dependency and
that non-orxtra producers and consumers can adopt independently. The first
external producer is the machine-wide disk-semantics tool described in the
companion todo `machine-disk-semantics-tool.md`, which interfaces with the
ecosystem ONLY through this bus.

Decision-origin legend: decisions marked `[%%]` were trust-adopted (owner
accepted a recommendation without deliberating) and are freely reversible;
unmarked decisions were deliberate owner picks; items marked OPEN are
deliberately undecided.

## Why extraction is well-prepared by the existing architecture

- The dependency layering already isolates the bus: dispatch depends only on
  protocols + trace; trace depends only on protocols; neither imports
  scheduler or overseer. Workflow execution is injected via the
  ActionExecutor protocol precisely so dispatch does not know what a
  workflow is.
- The modules are already independently installable packages; extraction
  adds independent identity and release cadence, not a first-ever
  untangling.
- The PG schemas are already externally owned by pgdesign
  (`schema/trace.toml`, `schema/dispatch.toml`); the contract can travel
  with the extracted tool.
- Events already carry a nullable run_id "for run-independent ingestion" —
  the store was designed to hold events unrelated to orxtra runs.
- If the standalone-repo option is chosen, `rlsbl monorepo extract` performs
  the mechanical move with history.

## Decisions

1. **Extract the event system into a separate tool** (deliberate).
2. **Cut line: trace + dispatch + incoming + identity move** `[%%]`. incoming
   (webhook ingestion with HMAC, cursor replay, SSE) is the language-neutral
   front door and belongs to the bus. identity is deliberately minimal (the
   principals table: FK target plus display name, instance-scoped kind
   registry) and travels so that attribution — the NOT NULL principal_id on
   every event — stays intact inside the bus.
3. **notification moves with the bus** (deliberate). It is generic
   event-to-principal delivery riding subscriptions; NotifyAction is one of
   the dispatch action types, and a bus shipping a subscription system whose
   notify action lives elsewhere would be incoherent. orxtra's inbox and
   human-review flows consume it as clients.
4. **auth stays orxtra-side** (consumers, scopes, credential hashing, ASGI
   middleware are orxtra-flavored), or becomes a thin client of the bus's
   surface — the exact boundary is an implementation decision for the
   extraction design.
5. **WorkflowAction stays orxtra-side**, re-registered through the existing
   ActionExecutor injection seam. The bus ships the generic actions
   (script/log/event/notify) plus the extension point.
6. **Producer interface: an official Go client library, shipped by the bus
   itself, wrapping the ingestion protocol** (deliberate). HTTP with a
   unix-socket transport for same-host producers; typed event structs and
   validation strictspec-generated from the same schema that generates the
   Python service side, so the two languages can never disagree about event
   shape. Rationale: exactly one write-path implementation exists (the
   bus's own); the PG schema stays private; producer event rates are modest
   (producers coalesce before publishing), so protocol overhead is noise.
   **Direct PG writing is NOT offered.** If a measured workload ever proves
   the protocol path inadequate, a direct-PG co-located mode may be added
   inside the same library as an explicit declared mode, accepting at that
   point the cost of write semantics (insert transaction, LISTEN/NOTIFY
   payload format, attribution enforcement) existing in two languages.
7. **License: MIT** (deliberate). Analysis: the bus is the adoption-seeking
   and most substitutable component (generic event infrastructure has many
   off-the-shelf alternatives); orxtra's differentiated substance —
   verification boundaries, budget enforcement, the Overseer — stays under
   BUSL-1.1 in orxtra. Caveat: this choice interacts with the OPEN home
   decision below — if the bus remains inside this BUSL-licensed monorepo,
   the per-subtree license divergence must be made explicit and visible.

## Open decisions (deliberately left open by the owner)

1. **Home.** Two real options: (a) a standalone repo (extracted with
   history via `rlsbl monorepo extract`; schema custody, i.e. the pgdesign
   TOML files, and CI move with it; orxtra declares the bus as a dependency
   like strictcli or pgdesign); (b) a releasable group INSIDE this monorepo
   with independent versioning (less churn; non-orxtra consumers point at
   this repo; the license divergence noted above applies). An earlier
   trust-adopted lean toward the standalone repo was explicitly reopened by
   the owner; both options are live.
2. **Name.** Open. Two candidates were checked against registries with the
   owner's explicit approval:
   - "omnibus": rejected — taken on both npm and PyPI, and it carries a
     strong existing association with Chef's Omnibus packaging tool.
   - "pgbus": viable for the primary registries — available on PyPI, and
     the Go client library lives at a GitHub module path (no central Go
     registry conflict; several unrelated GitHub repos share the name, but
     GitHub names are org-scoped). npm is taken (name-moniker collision
     with "pg-bus"), which would block a future TypeScript client under the
     same name — relevant because strictspec generates TypeScript and this
     monorepo already publishes to npm.
   Hard rule for any further candidates: NO registry contact (availability
   checks included) with a name the owner has not explicitly approved for
   checking; present candidates as plain text first.
3. **Attribution/auth boundary details**: where exactly the ephemeral
   AuthContext production sits once auth (orxtra) and identity (bus) are on
   opposite sides of the split; system-principal seeding; how the CLI's
   local-trust path maps onto the bus's surface.
4. **What the bus's own CLI surface is** (if any) versus library-only.

## Work sketch

- Design the split in detail: module moves, package renames, import-path
  updates in every orxtra consumer (scheduler, overseer, services, cli,
  mcp, a2a, agui, api, worker, notification consumers), protocols types
  that move versus stay.
- Schema custody: move or re-home the pgdesign TOML files per the home
  decision; regenerate; verify orxtra's migrations story across the split.
- Re-register WorkflowAction from orxtra through ActionExecutor; confirm no
  downward dependency appears.
- Build the Go client library (ingestion protocol client, unix-socket
  transport, source-principal registration, batching, persistent
  connection) with strictspec-generated types; conformance-test event-shape
  agreement between the Go and Python sides.
- Port or wrap the SSE/consumption side for non-Python consumers as needed
  by the first external producer's requirements.
- Releases: orxtra then depends on the extracted bus at a floor version
  (internal-dependency floor policy applies if standalone).

## Effort estimate

Large — a multi-session campaign. The module moves and import updates are
mechanical but wide; the genuinely new engineering is the Go client library
plus the strictspec schema for events; the attribution/auth boundary is the
main design risk. The extraction is well-prepared (see above), so the risk
is churn, not architecture.
