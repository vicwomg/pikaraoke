# CLAUDE.md

Guidance for Claude Code when working on PiKaraoke.

## Project Overview

PiKaraoke is a karaoke system for Raspberry Pi, Windows, macOS, and Linux. Web interface for YouTube song search, queuing, and playback with pitch shifting and streaming.

## Core Principles

**Single-owner maintainability:** Code clarity over documentation. Simplicity over flexibility. One source of truth.

## Refactoring

**Refactor iteratively as you work.** When touching code:

- Extract classes when a module has multiple responsibilities (like `Browser` was extracted from utilities)
- Extract functions when logic is repeated or a function exceeds ~50 lines
- Rename unclear variables/functions immediately
- Delete dead code - never comment it out
- Update related code consistently (no half-migrations)

**When to refactor:**

- Code you're modifying is hard to understand
- You're adding a third similar pattern (rule of three)
- A function/class is doing too many things

**When NOT to refactor:**

- Unrelated code "while you're in the area"
- Working code that you're not modifying
- To add flexibility you don't need yet

## Code Style

- PEP 8, 4 spaces, meaningful names
- Type hints required: `from __future__ import annotations`, modern syntax (`str | None`)
- Concise docstrings for public APIs - explain "why", not "how"
- No emoji or unicode emoji substitutes

## Filename Conventions

YouTube video filenames use exactly 11-character IDs:

- PiKaraoke format: `Title---dQw4w9WgXcQ.mp4` (triple dash)
- yt-dlp format: `Title [dQw4w9WgXcQ].mp4` (brackets)

Only support these two patterns.

## Error Handling

- Catch specific exceptions, never bare `except:`
- Log errors, never swallow silently
- Use context managers for resources

## Testing

- pytest with mocked external dependencies
- Test business logic and integration points
- Skip trivial getters/setters

## Code Quality

```bash
# Run pre-commit checks
uv run pre-commit run --config code_quality/.pre-commit-config.yaml --all-files
```

Tools: Black (100 char), isort, pycln, pylint, mdformat.

Never commit to `master` directly.

## What NOT to Do

- Add unrequested features
- Add error handling for impossible states
- Create abstractions for single uses
- Write speculative "future-proofing" code
- Commit debug prints or commented code
