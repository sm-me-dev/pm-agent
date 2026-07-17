# Security Policy

## Supported Versions

| Version | Supported |
|---|---|
| 0.x (latest) | ✅ |

## Reporting a Vulnerability

Open a [GitHub Security Advisory](https://github.com/sm-me-dev/pm-agent/security/advisories/new)
or email the maintainer directly. Do **not** file a public issue for security vulnerabilities.

You should receive a response within 72 hours. If the issue is confirmed,
a fix will be prepared and released as a patch version.

## Scope

- API key or token leaks through pm-agent output or logs.
- Unauthorized file system or git operations via action approval bypass.
- SQL injection through stored history, notes, or decision content.

Out of scope: vulnerabilities in the LLM provider endpoint, OpenAI library
issues, or OS-level sandboxing.
