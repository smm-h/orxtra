# Batched result delivery for concurrently launched subagents

## Context

When a parent launches N subagents in one go, their results come back one at a time,
each as its own completion event, in whatever order the children happen to finish.
The parent is re-invoked per completion and tends to process and react to each result
in isolation -- relaying it, drawing conclusions from it -- before the sibling results
that were commissioned as one batch have arrived. Cross-result synthesis (the reason
the batch was launched together) then happens piecemeal or not at all, and the
parent's output interleaves N partial reactions instead of one combined one.

## Problem

There is no way to say "these N children are one unit of work: hold their results and
deliver them to me together." Completion order is nondeterministic, so the parent's
behavior varies run to run, and a fast-finishing child can steer the parent's framing
before the slow, possibly contradicting sibling lands its report.

## Solutions

- **Barrier mode per launch group.** A flag on the spawn call (or on a group of spawn
  calls) marking the children as one batch: no result is delivered until all members
  have finished (or failed), then all are delivered in a single parent turn, in launch
  order. Pros: deterministic parent input, synthesis-by-construction, one wake-up
  instead of N. Cons: the parent loses the ability to react early to a fast failure;
  a hung child stalls the whole batch (needs a timeout story).
- **Configurable delivery policy** with at least `stream` (today's behavior) and
  `batch` (the barrier), chosen per launch group. Pros: keeps early-reaction workflows
  working; makes the batch behavior an explicit choice. Cons: one more knob; the
  default still has to be picked deliberately (no implicit default).
- **Windowed hybrid.** Deliver whatever has finished when the last member completes OR
  a per-group deadline passes, whichever is first, marking missing members explicitly.
  Pros: bounded stall. Cons: reintroduces partial batches, which is the problem, just
  rarer.

At minimum, make the barrier a configurable possibility; the per-group flag with an
explicit required choice fits the house style (mandatory flags over defaults).

## Affected area

The spawn/completion notification path and whatever surface configures a launch group.

## Effort

Small-to-medium: the buffering itself is simple; the timeout/failure semantics of a
held batch are the design work.
