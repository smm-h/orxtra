# Machine-wide disk-semantics tool for agents — founding design

A new tool (unnamed as of filing) that gives AI agents quick mastery over an
entire machine's disk: where everything is, what kinds of things exist, why
they exist, how to reclaim space safely, and how to watch for filesystem
events. Every other tool in the fleet is repo-scoped — you must point it at a
specific repository or it is blind. This tool is machine-scoped and
agent-first: install it on any machine and agents instantly get semantic
insight into its contents, plus recursive semantic functionality (directories
declaring facts about themselves) rather than only stats the agent could
derive itself.

## Context

Motivating incident: ~23 GB of regenerable build artifacts (about twenty
`node_modules` directories plus several Next.js dev caches) were reclaimed
across a directory of git worktrees with an ad-hoc bash script that looped
over top-level directories and deleted specific known-safe subdirectories in
each. The wish that followed: directories should passively declare their
safe-to-delete subdirectories in a manifest file, and a tool should
recursively discover all such manifests under a root, estimate the total
reclaimable size (dry run), or delete for real.

Two existing homes were evaluated for that feature and rejected:

- **dirstat** (Go, strictcli, single read-only `scan` command): its engine —
  parallel-classification walker, format classification, in-process gitignore
  matching — is exactly the right scanning core, but its spec deliberately
  commits to read-only operation, no persistence, and no discovery of files
  in the scanned tree. The feature contradicts all three founding
  commitments.
- An existing fleet deletion tool with archive-and-undelete semantics:
  mechanically opposed to space reclaim. It archives regular files by hard
  link (frees zero bytes at delete time) and directories by reading the whole
  tree into a tar+zstd container (cost proportional to the thing being
  removed, compressed copy left behind). Its consent model is built around
  exactly one irreversible command, and its correctness checks presume an
  archive entry exists. A no-archive mode would break its structure, not
  extend it.

Conclusion: the feature belongs to a new tool whose founding thesis is
machine-wide discovery, persistence, declared semantics, and actions. Safety
for deletion comes from pre-declaration (the manifest asserts regenerability
up front), not from reversibility (archiving) — a different safety model from
the existing deletion tool, which is why they are different tools.

## Novelty assessment (honest)

Machine-wide indexing has prior art: Spotlight/mdfind, Everything (NTFS MFT),
plocate/updatedb, GNOME Tracker, KDE Baloo, duc/ncdu. They answer "where is
stuff" and sometimes "what kind"; none answer "why", none accept declarations
from the filesystem's inhabitants, and none are built for an agent as the
consumer. The closest precedent for declared facts is CACHEDIR.TAG (a passive
per-directory marker that borg/tar/restic respect), which carries exactly one
bit of semantics. The novel core here is the combination of:

1. **Derived facts** — what a walker computes: sizes, formats, media, git
   repos, mount topology. Commodity, but must be fast at machine scale.
2. **Declared facts** — what no walker can derive: "this is regenerable build
   output", "this is an archived client project superseded by that one",
   "this bulk is curated media, do not touch". The reclaim manifest is the
   first species of this.
3. **An agent-first query-and-act surface** over the fusion of both.

## Decisions made

Marked by origin per the decision-origin convention: "deliberate" means the
user stated it directly; "recommended-pick" means the user accepted a
recommended option and the decision is weakly held — walk it back freely if
evidence goes against it.

1. **New tool, machine-scoped, agent-first** (deliberate).
2. **Linux-only** (deliberate). Deletes macOS FSEvents / Windows USN surface
   from the design entirely; frees the design to use fanotify, /proc mount
   info, io_uring.
3. **Watch/notify is a founding architectural component, designed in from
   the start** (deliberate). Not an internal freshness optimization.
4. **Reclaim frees space for real** (deliberate): direct removal, no
   archiving. Safety via declarations and hard invariants, not undo.
5. **The new tool consumes dirstat's engine, extracted into an importable
   library** (recommended-pick). dirstat currently keeps the engine under
   `internal/`; promotion to an exported library is a real API-design
   refactor in dirstat, after which dirstat remains the finished, focused
   stats instrument and both binaries share the scanning core (fleet pattern:
   strictcli/strictspec under many tools).
6. **One declaration file name per directory, capability-scoped sections**
   (recommended-pick). A `[reclaim]` section now; purpose/project/media
   sections later. One name for agents to learn, one discovery pass. Schema
   defined and validated via strictspec with a per-document format_version.
7. **First release scope: persistent index + orientation queries + reclaim
   as the only mutating verb** (recommended-pick). "Know everything, delete
   only what was declared disposable."
8. **Freshness model: SQLite index + explicit on-demand/scheduled refresh**
   (recommended-pick). Queries read the index and report its age honestly.
   Watchers arrive later as a deliberate explicit mode; no mode is ever a
   silent fallback for another (no-silent-degradation rule).
9. **Reclaim safety invariant** (recommended-pick): inside a git repository,
   targets must be gitignored, verified in-process — tracked or unignored
   paths are refused, making "reclaim ate uncommitted work" structurally
   impossible. Outside any repository, each manifest entry must carry an
   explicit marker acknowledging there is no VCS backstop; a missing marker
   is a hard error. No bypass flags anywhere.
10. **Reclaim targets directories only in the first release**
    (recommended-pick). File globs later as an additive schema change.
11. **Predicates are existence-only in the first release**
    (recommended-pick): an entry may require that a relative path exists
    (e.g. the matched directory's parent must contain `.git`). Age/size
    conditions later if real manifests demand them.
12. **Declarative data, not an executed language** (settled in discussion).
    TOML with globs and predicates; sexp/starlark rejected. The motivating
    "loop over top-level dirs" case is fully expressed by globs
    (`*/node_modules`) plus an existence predicate. An executed manifest
    makes "what will this delete" answerable only by evaluating it, and
    recursive discovery means executing manifests found in trees the user
    did not write (cloned repositories) — declarations must be readable and
    strictly validatable instead.

## Design notes (worked out in discussion)

### Manifest sketch

```toml
# Regenerable build artifacts across git worktrees.
[[reclaim]]
glob = "*/node_modules"
require = [".git"]        # matched dir's parent must contain .git
restore = "npm ci"        # documentation only, never executed
```

Validation: closed key set, hard error on unknown keys, everything validated
before any action (fleet style). Confinement: globs are relative, targets
must resolve inside the manifest's own directory, no absolute paths, no
`..`, no symlink escape — worst case a hostile manifest in a cloned repo
declares its own contents deletable, and the dry run shows it.

### Reclaim command shape

Discover manifests by exact filename recursively; validate every manifest
first (hard error on any invalid one before touching anything); resolve
targets; size them. Dry run prints per-target sizes, per-manifest subtotals,
grand total. Execution is a strictcli `mutating` + `consequential` command,
so the framework provides `--dry-run` (record instead of perform) and the
consent prompt / `--approve-consequential` natively. The discovery walk must
descend into gitignored and conventionally-excluded directories — manifests
and targets live exactly there.

### Filesystem truth (Linux, and btrfs specifically)

- Report **apparent size and allocated blocks (st_blocks) side by side**.
  On btrfs with compression (e.g. compress=zstd), apparent size
  systematically overstates what df will return after deletion; a
  compressible `node_modules` occupies less disk than its apparent bytes.
- Reflinks, snapshots, and dedup share extents, so naive per-file block sums
  double-count; df-level truth is per-filesystem, not per-file-sum. The spec
  should state this honestly rather than pretend per-file precision
  (`compsize` is prior art for btrfs compressed-usage accounting).
- Model mount topology as a first-class fact: mounts, pools, subvolumes,
  tmpfs, overlayfs (container layers), bind mounts. Never blindly cross
  device boundaries. Example: root and home as separate subvolumes on one
  btrfs pool means free space is a pool-level number — reclaiming under home
  frees space for root too.

### Freshness ladder (all explicit modes, no silent fallback)

1. On-demand subtree scan — always available.
2. Scheduled incremental re-walk (the updatedb model). Full machine re-walks
   are faster than intuition suggests: plocate walks entire systems in
   seconds-to-a-minute warm; a parallel stat-walk of a few million inodes on
   NVMe is tens of seconds. This tier plausibly remains the permanent
   default.
3. Later: root daemon on fanotify ("file access notify"; kernel 5.1+
   provides FAN_MARK_FILESYSTEM + FAN_REPORT_DFID_NAME for whole-filesystem
   create/delete/move/write events carrying parent-directory handle plus
   entry name). Requires CAP_SYS_ADMIN, hence a systemd service with an
   unprivileged CLI reading the index — a real privilege-boundary design.
   Event storms (a package install creates hundreds of thousands of files)
   are coalesced to "subtree dirty, re-walk lazily".
4. Optional FS-specific accelerator: btrfs generation numbers /
   `btrfs subvolume find-new` enumerate files changed since a generation
   without walking (ZFS has `zfs diff`). ext4/xfs have no such primitive —
   there, it is walk or fanotify, nothing else.

inotify is not viable machine-wide (one watch per directory, ~1 KB kernel
memory each, arming walk of every directory, race on new directories) but is
fine for bounded subtrees — see watch backends below.

### Watch/notify surface (founding component; revised after the event-bus direction)

Revised: this monorepo's event system (trace + dispatch + incoming +
notification + minimal identity) is planned for extraction into a standalone
event-bus tool — see the companion todo `event-bus-extraction.md`. The disk
tool therefore does NOT grow its own subscription system. Its watch role
splits into two explicit modes; per the no-silent-degradation rule the
presence of bus configuration selects the mode, and a configured but
unreachable bus is a hard error, never a fallback to the other mode:

- **Standalone mode** (no bus, no PostgreSQL — the tool stays complete on
  any machine, preserving the install-anywhere thesis): a blocking `watch`
  command evaluates conditions locally over a bounded subtree (unprivileged
  recursive inotify) and exits with a structured report when the condition
  fires. Blocking-command delivery is agent-shaped: agents are resumed by
  process exit, so a blocking command in a background shell IS the
  notification mechanism.
- **Bus-connected mode**: the disk tool is an event PRODUCER and domain
  evaluator, not a subscription system. It registers with the bus as a
  source principal and publishes coalesced filesystem events plus derived
  domain events ("download quiesced", "subtree crossed a size bound")
  through the bus's official Go client library. That library wraps the bus
  ingestion protocol (HTTP, with a unix-socket transport for same-host
  producers); event types and validation are strictspec-generated from the
  same schema that generates the bus's Python service side, so producer and
  service can never disagree about event shape. Direct database writing is
  NOT part of the contract (reserved as a possible future explicit mode only
  if a measured need arises). Subscriptions, filter predicates, action
  chains, accumulator buffering, and notification delivery are all bus-side.

Condition vocabulary — three closed kinds, hard error on anything else —
and its split across the modes:

- **Change** (any/create/delete/modify/move under a path, optionally
  glob-filtered): plain event filtering — evaluated locally in standalone
  mode, expressed as bus filter predicates when connected.
- **Quiescence** ("this file/dir finished being written", e.g. a download
  completing): close-after-write events plus a stability window (size
  unchanged for N seconds), plus rename-awareness — downloaders write
  `*.part`/`*.crdownload` and rename into place, so completion is
  "close-write or rename-into-existence of the final name, then stable".
  That heuristic belongs in the disk tool, not in every agent's head.
- **Threshold**: subtree size or entry count crossing a bound in either
  direction; requires the incremental, event-fed index. Threshold-watching
  is index-powered, not a sibling feature.

Quiescence and threshold are disk-domain logic in both modes: the disk tool
evaluates them and, when connected, emits the outcome as derived events. The
raw fanotify/inotify firehose never leaves the disk tool — coalescing to
subtree-dirty summaries and derived events happens before anything is
published, so bus-facing event rates are modest.

Event-acquisition backends, chosen explicitly, backend in use always
declared in output:

- Scoped watch, unprivileged: recursive inotify over one bounded subtree.
  Shippable early, no daemon, no root.
- Machine-wide: the fanotify root daemon, which then serves both index
  freshness and event production.

### Other founding stances

- Index in SQLite under the XDG data dir. Per-directory aggregates by
  default; per-file rows only where a capability needs them (media
  inventory, large-file tracking) with retention rules per fact kind —
  avoid desktop-indexer-scale bloat.
- The index is a map of everything on the machine: sensitive artifact.
  Local-only, no network; consider inheriting dirstat's import ban on
  net/http as a structural guarantee.
- strictcli provides the CLI framework, the effects regime, and MCP exposure
  of commands (`--mcp`) — the agent surface comes essentially for free.
- Future capability directions once the manifest gains sections: "where are
  all the media files" (derived, via format classification with location
  retention), "where are all the projects stored, how and why" (derived
  facts like git remotes and manifests, augmented by declared purpose/status
  sections). A repo-activity journaling tool in the fleet is a natural
  consumer of the project inventory ("what happened on this machine's repos
  this week").

## Open items

1. **Home — deliberately left open by the owner.** Two real options:
   (a) a standalone repo; (b) a member of THIS monorepo. Trade-offs noted in
   discussion: standalone preserves full independence (the tool interfaces
   with the ecosystem only through the event bus), keeps the license
   decision free of this monorepo's BUSL-1.1 association, and avoids a lone
   Go module inside a uniform Python workspace — and a Go module import path
   is permanent identity, so a monorepo path bakes this monorepo's name into
   the tool's address forever. Monorepo membership reduces repo sprawl,
   inherits conventions, and skips inventing a standalone brand (though the
   sub-project, module path, and the command name agents type still need
   names).
2. **Name — deliberately left open**, interacts with the home decision. A
   four-candidate slate was reviewed in discussion without resolution.
   Hard rule: NO registry contact (availability checks included) with any
   candidate the owner has not explicitly approved for checking; a violation
   of exactly this occurred once during the design discussion and was
   stopped. Present candidates as plain text first.
3. **License — deliberately left open**, also interacts with the home
   decision (this monorepo is BUSL-1.1; the standalone fleet tools are
   mostly MIT). Never defaulted.
4. **Manifest filename** — likely derived from the tool's name; blocked on
   the name.
5. **dirstat engine extraction specifics**: what API surface the exported
   library exposes (walker, classification, gitignore — some or all), and
   how dirstat's own spec/docs account for the extraction.
6. **Manifest nesting/overlap rule.** Proposal on the table, not ratified:
   dedup nested targets; when an outer manifest's target contains an inner
   manifest, the outer target wins (the inner one's targets are inside the
   deleted tree anyway).
7. **CLI surface**: command names and shapes for index refresh, queries,
   reclaim, watch.
8. **Whether reclaim execution writes an operation log** of its own (what
   was freed, where, per which manifest) beyond its stdout/JSON output.
9. **Daemon packaging** (systemd unit, index file ownership/permissions
   between root daemon and unprivileged CLI) — relevant only when the
   fanotify tier is built.

## Affected / related

- dirstat: the engine-extraction refactor happens there (its `internal/scan`
  and related packages promoted to an importable library).
- strictcli: CLI framework, effects regime, MCP exposure.
- strictspec: the single authority for the manifest document schema, and for
  the bus event types shared between the disk tool (Go) and the bus service
  (Python).
- The event bus extracted from this monorepo (companion todo
  `event-bus-extraction.md`): the disk tool's bus-connected mode is its
  first external producer.

## Amendment record

This file was amended once after filing, on the owner's explicit
instruction (overriding the usual filed-todo immutability): the watch/notify
section was rewritten for the event-bus direction (producer role, two
explicit modes, Go client library over the bus ingestion protocol), and the
open items for home, name, and license were rewritten to record that the
owner deliberately left them open — including monorepo membership as a real
option. Everything else is as originally filed.

## Effort estimate

- dirstat engine extraction: small-to-medium (API design plus package
  moves; dirstat's own behavior unchanged).
- First release (index + orientation queries + reclaim): medium-to-large;
  several sessions (index schema, walker parallelization, mount topology,
  manifest validation, reclaim command with the two safety invariants,
  tests throughout).
- Watch surface, scoped-inotify tier: medium; independent of the daemon.
- fanotify root daemon + machine-wide subscriptions: large; its own
  campaign.
