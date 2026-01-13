# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PiKaraoke is a "KTV"-style karaoke system that runs on Raspberry Pi, Windows, macOS, and Linux. It provides a web interface for searching YouTube, queuing songs, and playing karaoke videos with features like pitch shifting, background music, and real-time streaming.

### Filename Conventions

PiKaraoke uses yt-dlp to download karaoke videos from YouTube. Files follow strict naming patterns:

**PiKaraoke Format (Explicit):**

```
Song Title---dQw4w9WgXcQ.mp4
Artist - Song Title---abc123defgh.cdg
```

**yt-dlp Default Format:**

```
Song Title [dQw4w9WgXcQ].mp4
Artist - Song Title [abc123defgh].mkv
```

**Rules:**

- YouTube IDs are **exactly 11 characters**: `[A-Za-z0-9_-]{11}`
- PiKaraoke uses **triple dash** (`---`) before the YouTube ID
- yt-dlp default uses **brackets** (`[]`) around the YouTube ID
- **NEVER** implement support for hypothetical formats that don't exist from yt-dlp downloads
- **ONLY** support these two patterns when extracting YouTube IDs from filenames

## Single-Owner Maintainability Philosophy

This project is maintained by a single code owner. All code and documentation decisions prioritize:

- **Self-documenting code over extensive documentation**: Code clarity reduces documentation burden
- **Simplicity over flexibility**: Solve current problems, not hypothetical future ones
- **Consolidation over duplication**: One source of truth for each concept
- **Actionability over completeness**: Documentation must serve immediate practical needs

**Code is the primary documentation.** Written docs supplement code, never duplicate it.

## Code Style and Formatting

- **MUST** use meaningful, descriptive variable and function names
- **MUST** follow PEP 8 style guidelines
- **MUST** use 4 spaces for indentation (never tabs)
- **NEVER** use emoji, or unicode that emulates emoji (e.g. âœ“, âœ—). The only exception is when writing tests and testing the impact of multibyte characters.

## Type Hinting

- **MUST** use `from __future__ import annotations` at the top of files with type hints
- **MUST** use modern union syntax (`str | None`) instead of `Union[str, None]`
- **MUST** include type hints for all function parameters and return values
- Use `TYPE_CHECKING` to avoid circular imports in type hints

Example:

```python
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pikaraoke.karaoke import Karaoke


def process_queue(karaoke: Karaoke) -> str | None:
    """Process items from the queue."""
```

## Documentation

### Code Documentation (Docstrings)

- **MUST** include docstrings for all public functions, classes, and methods
- **MUST** document function parameters, return values, and exceptions raised
- **MUST** keep docstrings concise - explain "why" and "what", not "how"
- **NEVER** duplicate information that type hints already convey
- **NEVER** write obvious docstrings that just restate the function name
- Include examples in docstrings ONLY for non-obvious behavior

Good docstring (concise, adds value):

```python
def scan_directory(self, directory: str) -> int:
    """Scan directory for song files and replace the current list.

    Args:
        directory: Path to directory to scan.

    Returns:
        Number of songs found.
    """
```

Bad docstring (verbose, obvious):

```python
def scan_directory(self, directory: str) -> int:
    """This function scans a directory for song files.

    It will iterate through all files in the provided directory path,
    check each file to see if it's a valid song file format, and then
    add those files to the song list, replacing any previously existing
    song list that was there before.

    Args:
        directory: This is a string that represents the path to the
                   directory that you want to scan for song files.

    Returns:
        An integer representing the total count of how many songs
        were found during the scanning process.
    """
```

### Written Documentation (Markdown Files)

- **MUST** have a single source of truth for each concept
- **NEVER** duplicate information across multiple markdown files
- **MUST** keep documentation clear and practical for developers
- **MUST** include code snippets and examples to illustrate concepts
- **MUST** update documentation immediately when code changes
- **MUST** delete outdated documentation immediately when code changes
- **SHOULD** prefer linking to code examples over explaining code in prose

Documentation hierarchy (single owner):

1. **Code + docstrings** - Primary source of truth for implementation
2. **README.md** - User-facing setup and usage only
3. **docs/** - Technical documentation, design decisions, implementation guides
   - Keep planning docs after implementation if they explain "why"
   - Include code snippets and practical examples
   - Write for developers who need to understand or extend the system
4. **Architecture decisions** - Document non-obvious patterns and design choices

### Documentation Maintenance

When code changes:

- **MUST** update affected docstrings immediately in the same commit
- **MUST** update technical docs that explain the implementation
- **MUST** delete documentation that's no longer accurate
- **SHOULD** check if README.md sections are still accurate
- **NEVER** leave outdated docs with "TODO: Update this"

Documentation review - periodically perform a documentation audit:

1. Remove or consolidate duplicate explanations
2. Verify README.md reflects current installation/usage
3. Update implementation docs to reflect current state
4. Delete docs that no longer serve a purpose

**Documentation should help developers understand:**

- Why design decisions were made
- How complex systems work
- What patterns to follow when extending the code
- Key implementation details that aren't obvious from code alone

## Documentation Anti-Patterns

As a single-owner project, avoid these documentation pitfalls:

### NEVER: Tutorial-Style Documentation

- **Bad**: "Step 1: Import the module. Step 2: Create an instance..."
- **Good**: Working code example with minimal explanation

### NEVER: Duplicate Information

- **Bad**: Explaining the same API in README.md, docs/api.md, and inline comments
- **Good**: One docstring in code, README links to code

### NEVER: Speculative Documentation

- **Bad**: "In the future, this might support X, Y, Z..."
- **Good**: Document what exists now

### DO: Keep Useful Implementation Documentation

- **Good**: Keep planning docs that explain design decisions and rationale
- **Good**: Include code snippets showing how features work
- **Bad**: Keep outdated docs that no longer match the implementation
- **Bad**: Write documentation that just duplicates what's in the code

### PREFER: Self-Documenting Code

- **Better**: Rename `process(d, f)` to `process_song_metadata(data, filename)`
- **Worse**: Keep vague names and write comments explaining them

## Error Handling

- **NEVER** silently swallow exceptions without logging
- **MUST** never use bare `except:` clauses
- **MUST** catch specific exceptions rather than broad exception types
- **MUST** use context managers (`with` statements) for resource cleanup
- Provide meaningful error messages

## Function Design

- **MUST** keep functions focused on a single responsibility
- **MUST** keep code simple and maintainable by a single code owner
- **MUST** solve the immediate problem, not hypothetical future problems
- **NEVER** use mutable objects (lists, dicts) as default argument values
- **NEVER** add configuration/flexibility that isn't immediately needed
- Limit function parameters to 5 or fewer
- Return early to reduce nesting
- Favor brevity: prefer concise, readable implementations over verbose code
- Delete dead code immediately - don't comment it out "just in case"

Single-owner mindset:

- If you won't remember why in 6 months, the code isn't clear enough
- Three similar lines are better than a premature abstraction
- Add helpers when third duplication appears, not before

## Class Design

- **MUST** keep classes focused on a single responsibility
- **MUST** keep `__init__` simple; avoid complex logic
- Use dataclasses for simple data containers
- Prefer composition over inheritance
- Avoid creating additional class functions if they are not necessary
- Use `@property` for computed attributes

## Testing

- **MUST** write unit tests for all new functions and classes
- **MUST** mock external dependencies (APIs, databases, file systems)
- **MUST** use pytest as the testing framework
- **MUST** test realistic failure scenarios that could actually happen
- **NEVER** run tests you generate without first saving them as their own discrete file
- **NEVER** delete files created as a part of testing
- **NEVER** write tests for impossible states or trivial getters/setters
- Ensure the folder used for test outputs is present in `.gitignore`
- Follow the Arrange-Act-Assert pattern
- Do not commit commented-out tests

Single-owner testing priorities:

1. Business logic and data transformations (high value)
2. Integration points with external systems (high risk)
3. Complex conditional logic (hard to reason about)
4. Skip: trivial property access, framework wrappers, obvious code

## Imports and Dependencies

- **MUST** avoid wildcard imports (`from module import *`)
- **MUST** document dependencies in `pyproject.toml`
- **MUST** prefer well-maintained libraries over custom implementations when appropriate
- Use `uv` for fast package management and dependency resolution
- Organize imports: standard library, third-party, local imports
- Use `isort` to automate import formatting

## Python Best Practices

- **NEVER** use mutable default arguments
- **MUST** use context managers (`with` statement) for file/resource management
- **MUST** use `is` for comparing with `None`, `True`, `False`
- **MUST** use f-strings for string formatting
- **EXCEPTION**: Use percent formatting (`%s`, `%d`) with i18n functions (`_()`, `gettext()`) for translation tool compatibility
  - Example: `_("Volume: %s") % value` instead of `_(f"Volume: {value}")`
- Use list comprehensions and generator expressions
- Use `enumerate()` instead of manual counter variables

## Delivery Standards (Single-Owner Context)

### Fully Functional, Not Gold-Plated

- **MUST** deliver working, tested code for the requested feature
- **MUST** handle realistic error cases (user input, network failures)
- **NEVER** add error handling for impossible states
- **NEVER** add features that weren't requested
- **NEVER** refactor unrelated code "while you're in the area"

### Definition of "Fully Functional"

A feature is complete when:

1. It works for the specified use case
2. It handles expected error conditions gracefully
3. It has tests covering core functionality
4. It follows project conventions (type hints, formatting, etc.)
5. Related code is updated consistently (no half-migrations)

A feature is NOT complete when:

- It works "most of the time" but fails on edge cases you know about
- Tests are skipped because "it's obvious it works"
- New patterns are introduced without updating similar existing code

### What NOT to Include

- Feature flags for stable functionality
- Backwards compatibility shims for internal refactors
- Extensive configuration for features with one use case
- Abstraction layers for single implementations
- Documentation beyond docstrings and README updates

## Version Control

- **MUST** write clear, descriptive commit messages
- **NEVER** commit commented-out code; delete it
- **NEVER** commit debug print statements or breakpoints
- **NEVER** commit credentials or sensitive data

## Code Quality

### Pre-commit Hooks

The project uses pre-commit hooks defined in `code_quality/.pre-commit-config.yaml`:

```bash
# Run all pre-commit checks
pre-commit run --config code_quality/.pre-commit-config.yaml --all-files

# Install hooks
pre-commit install --config code_quality/.pre-commit-config.yaml
```

### Code Formatting & Linting

- **Black**: Formats Python code with 100 character line length
- **isort**: Sorts imports with black profile
- **pycln**: Removes unused imports
- **pylint**: Lints Python code
- **mdformat**: Formats markdown files (with mdformat-black for Python code blocks)

Note: Never commit directly to the `master` branch - the pre-commit hook will prevent this.

## Documentation and Markdown Files

### Python Code in Markdown

When writing Python code examples in markdown files (e.g., implementation plans, READMEs):

- **MUST** ensure all Python code blocks are valid and will pass pre-commit checks
- **MUST** follow Black formatting (100 char line length) in code blocks
- **MUST** use proper import organization (stdlib, third-party, local)
- **MUST** include type hints using modern syntax (`str | None`)
- **NEVER** leave syntax errors or incomplete code in markdown examples

**Why:** The pre-commit hook runs `mdformat` with `mdformat-black`, which will:

1. Parse Python code blocks in markdown files
2. Format them using Black's rules
3. Fail the commit if code is invalid or improperly formatted

**Testing markdown before commit:**

```bash
# Test markdown formatting (including Python code blocks)
mdformat --check docs/*.md

# Auto-fix markdown formatting
mdformat docs/*.md
```

**Example - CORRECT Python in markdown:**

````markdown
```python
from __future__ import annotations

import os
from typing import TYPE_CHECKING


def parse_filename(filename: str) -> dict[str, str | None]:
    """Parse filename into metadata.

    Args:
        filename: Song filename.

    Returns:
        Dict with artist, title, etc.
    """
    clean = os.path.splitext(filename)[0]
    return {"artist": None, "title": clean}
```
````

**Example - INCORRECT (will fail pre-commit):**

````markdown
```python
# Missing imports, no type hints, incomplete code
def parse_filename(filename):
    clean = os.path.splitext(filename)[0]
    # TODO: finish this
```
````

### Markdown Best Practices

- **MUST** use consistent heading levels (no skipping from # to ###)
- **MUST** use fenced code blocks with language specifiers (`python, `bash, etc.)
- **NEVER** use tabs for indentation (4 spaces only)
- **MUST** ensure all links are valid
- Keep line length reasonable (aim for 100 chars, but not enforced in prose)
