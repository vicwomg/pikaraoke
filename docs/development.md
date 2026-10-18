# Development guide

This is a single-owner project. Contributions should prioritize code clarity and simplicity over flexibility - the maintainer needs to read, understand, and modify every line.

## Length and clarity

Code comments, issues and PR descriptions follow one rule: say what the reader needs in order to act, then stop. Re-read before you post, and delete every sentence that would not change what they do.

- Ceilings, not targets: two sentences for a comment, and most code needs none; 250 words for an issue or PR description.
- One sentence per bullet. Anything that enumerates - changes made, findings, steps - is a list, not a paragraph.
- Evidence - logs, measurement tables, diffs, command output - goes in a follow-up comment. Carry the one number that makes the argument into the body and leave the rest below.
- Say what the code or the diff cannot. A comment restating the line above it, or a bullet restating its own heading, is noise.

## Before you start

### Start with an issue

Raise an issue and let it be discussed before writing code. Unsolicited PRs usually get closed - not because the code is bad, but because the idea was never agreed or duplicates work underway. An issue costs you an afternoon and the maintainer a few minutes; a rejected PR costs you a weekend and the maintainer an evening.

The issue should answer four questions.

**Who benefits, and what can they do afterwards that they can't do today?** Name a real person - the host, a guest in the room, whoever maintains the code. "Improves security" and "more maintainable" aren't answers on their own.

**What is it costing them now?** A bug users hit, a feature that was requested, friction that's actually slowing the project down. Concrete, not theoretical.

**Does it already exist?** The project spans Python, Jinja templates and client-side JavaScript, so the same concern - cleaning a song title, checking a permission - may already be handled in another layer. Search the codebase and the open PRs. New code should extend what exists, not sit alongside it.

**What is the plan?** Roughly what changes and by how much, so the shape can be agreed before anyone writes code.

Use the issue templates: they prompt for the platform and reproduction detail a maintainer would otherwise have to ask for.

### Keep the scope to one issue

PRs are squash-merged, so unrelated changes lose their context in the release notes. Anything worth improving outside your PR's scope goes in a separate issue.

## Setting up

Pikaraoke uses [uv](https://github.com/astral-sh/uv) for dependency management and local development. Install it, clone this repo, then from the pikaraoke directory:

```
# Install all dependencies and run pikaraoke from the local codebase
uv run pikaraoke
```

## Writing the change

### Code expectations

- Only modify what's needed for the task at hand, and don't refactor nearby code "while you're in the area"
- Solve the problem as directly as possible - prefer straightforward code over clever or generic solutions
- Don't add error handling, abstractions, or configurability beyond what the change requires
- Apply the rule of three: don't extract a helper or abstraction until a pattern appears three times
- Delete dead code rather than commenting it out, and don't leave debug prints or TODOs behind
- If your change is security-related, read `pikaraoke/lib/auth.py` and `pikaraoke/lib/admin_auth.py` first. Authorization is already default-closed: every route is refused unless marked `@public`. Extend that model rather than adding a second one alongside it.

### Style

- PEP 8, Black (100 char line length), isort, pycln - pre-commit catches most of it automatically
- Type hints required, using modern syntax (`str | None`). Python 3.10+ is the minimum, so `from __future__ import annotations` is not needed in new code.
- Concise docstrings for public APIs - explain "why", not "how"
- A comment earns its place with a measured number, a rejected alternative, or a platform quirk. State it once, at the definition, and prune any comment an edit makes wrong.

## Before you push

### Pre-commit checks

CI rejects commits that fail the code-quality gates, so run them first - they auto-fix where they can:

```
uv run pre-commit run --config code_quality/.pre-commit-config.yaml --all-files
```

To run them on every commit automatically:

```
pre-commit install --config code_quality/.pre-commit-config.yaml
```

### Tests

All tests must pass before submitting a PR:

```
uv run pytest
```

If your change affects behavior, add or update tests to cover it. Mock external I/O and subprocess calls - don't rely on network access or file system side effects.

### Commit messages

Commit messages must follow the commitlint format, `<type>: <description>`. See [this guide by arunkumarvallal](https://arunkumarvallal.medium.com/become-a-pro-at-commit-messages-using-commitlint-56dab86333b3) for the conventions.

```
build: update npm dependency
chore: update the pre-commit hook revisions
ci: add circleci configuration file
docs: fix typo in foo.md and bar.md
feat: allow provided config object to extend other configs
fix: resolve issue with incorrect data rendering
perf: optimize database query for faster response times
refactor: reorganize code structure for better readability
style: format code according to Prettier standards
test: add unit tests for user authentication
```

Those types and `revert` are the only valid ones; anything else - `security:`, `hotfix:`, `update:` - is rejected. Commitlint runs in CI only, so pre-commit won't warn you locally.

`feat`, `fix`, `perf` and `docs` reach the release notes; the rest are hidden. Use `chore` for repo housekeeping no user will ever read about.

## Opening the PR

Link the issue the PR implements. The title becomes the squash-merge subject, `<title> (#N)`, so it has to be a valid commitlint header on its own - a leading Markdown `#` comments the whole subject out, fails commitlint, and drops the commit from the changelog.

The description should carry:

- One line on who benefits and what changes for them.
- One bullet per change, saying what moved and why.
- A test plan: a short checklist of what you actually ran, specific to this change.

A copied or generic checklist is worse than none. "Set an admin password, confirmed the control still worked; retried in a private window with no session, confirmed it was refused" is worth more than any number of ticks beside "no functional regression". Don't list `uv run pytest` or the pre-commit run either - CI runs both on every push, so the checklist is for what you did by hand.

On approval the PR is squashed into a single commit on master, for the next release.

## Release process

For the maintainer. Before releasing, consider:

- [ ] Do translations need updating?
- [ ] Do you need to bump the minimum yt-dlp version?
- [ ] Are there dependabot updates that should be included?

When master is ready, fire off a release from `Actions tab > release-please > Run workflow`. This creates a release PR with the changelist; review it, then merge. Run the same workflow again and Github actions handles the releasing to PyPi and docker, and creates the new release page summary.
