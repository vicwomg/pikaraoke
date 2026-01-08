# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PiKaraoke is a "KTV"-style karaoke system that runs on Raspberry Pi, Windows, macOS, and Linux. It provides a web interface for searching YouTube, queuing songs, and playing karaoke videos with features like pitch shifting, background music, and real-time streaming.

## Code Style and Formatting

- **MUST** use meaningful, descriptive variable and function names
- **MUST** follow PEP 8 style guidelines
- **MUST** use 4 spaces for indentation (never tabs)
- **NEVER** use emoji, or unicode that emulates emoji (e.g. ✓, ✗). The only exception is when writing tests and testing the impact of multibyte characters.

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

- **MUST** include docstrings for all public functions, classes, and methods
- **MUST** document function parameters, return values, and exceptions raised
- Keep comments up-to-date with code changes
- Include examples in docstrings for complex functions

Example docstring:

```python
def scan_directory(self, directory: str) -> int:
    """Scan a directory for song files and replace the current list.

    Args:
        directory: Path to directory to scan.

    Returns:
        Number of songs found.
    """
```

## Error Handling

- **NEVER** silently swallow exceptions without logging
- **MUST** never use bare `except:` clauses
- **MUST** catch specific exceptions rather than broad exception types
- **MUST** use context managers (`with` statements) for resource cleanup
- Provide meaningful error messages

## Function Design

- **MUST** keep functions focused on a single responsibility
- **NEVER** use mutable objects (lists, dicts) as default argument values
- Limit function parameters to 5 or fewer
- Return early to reduce nesting

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
- **NEVER** run tests you generate without first saving them as their own discrete file
- **NEVER** delete files created as a part of testing.
- Ensure the folder used for test outputs is present in `.gitignore`
- Follow the Arrange-Act-Assert pattern
- Do not commit commented-out tests

## Imports and Dependencies

- **MUST** avoid wildcard imports (`from module import *`)
- **MUST** document dependencies in `pyproject.toml`
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
- **mdformat**: Formats markdown files

Note: Never commit directly to the `master` branch - the pre-commit hook will prevent this.
