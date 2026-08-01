# Improve source docstring coverage

## Current state

694 of 1,016 public functions lack docstrings (31.7% coverage).

## Worst modules

- trace: 226 missing (11% covered)
- scheduler: 82 missing
- tool: 64 missing
- protocols: 52 missing
- services: 42 missing
- dispatch: 41 missing

## Why it matters

API reference pages are generated but show skeleton-only entries for undocumented symbols. Docstrings feed the ref directive output -- without them, the auto-generated pages are empty shells.

## Approach

Prioritize by consumer impact: trace and scheduler are the most-used modules. Add concise one-line docstrings to all public functions, focusing on what the function does, not implementation details.
