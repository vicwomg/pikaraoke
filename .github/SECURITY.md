# Security Policy

## Reporting a vulnerability

Please do not report security issues in public issues, pull requests or
discussions.

Report them privately through GitHub instead:
**[Report a vulnerability](https://github.com/vicwomg/pikaraoke/security/advisories/new)**
(Security tab → Report a vulnerability).

Please include:

- the version or commit affected
- how PiKaraoke was run (flags, and whether it was reachable beyond the local
  network, e.g. through `--url` and a reverse proxy)
- steps to reproduce, or a proof of concept
- what an attacker can do with it

## What to expect

PiKaraoke is maintained by volunteers, so these are aims rather than guarantees:

- an acknowledgement within 7 days
- a fix, or an agreed plan, within 90 days
- credit in the advisory and release notes, unless you would rather not be named

Please give us the chance to ship a fix before disclosing publicly.

## Supported versions

Fixes go into the latest release only.

## Scope

PiKaraoke is often reachable by anyone who can scan the QR code, and sometimes
from the internet through a reverse proxy, so issues that a guest without the
admin password can trigger are in scope.

Running with no admin password makes every visitor an admin by design. That
alone is not a vulnerability, but a way to act as admin when a password *is*
set is.
