# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.x     | Yes       |

## Reporting a vulnerability

**Please do not open a public GitHub issue for security vulnerabilities.**

Report vulnerabilities by email to **fodorad201@gmail.com** with the subject line
`[Annie] Security vulnerability`.

Include:
- A description of the vulnerability and its potential impact.
- Steps to reproduce or a minimal proof-of-concept.
- Your suggested fix, if you have one.

You can expect an acknowledgement within **72 hours** and a status update within **7 days**.
If a fix is warranted, it will be released as a patch version and credited to you
(unless you prefer anonymity).

## Scope note

Annie is a **single-user, local-first** tool. By default it binds to localhost and
reads/writes files inside the configured data root. Do not expose an Annie instance
to an untrusted network without putting it behind your own authentication layer.
