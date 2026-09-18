# Security Policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting or contact the repository owner privately. Do not open a public issue containing credentials, tenant URLs, session contents, or exploit details.

## Credential handling

- Keep Jira credentials outside the repository.
- Prefer `HERMES_JIRA_CONFIG_FILE` or environment variables.
- The settings JSON is intentionally credential-free.
- Never include API tokens, authorization headers, cookies, real issue payloads, or session transcripts in bug reports.

## Attachment proxy

Attachment previews are fetched by the backend only after verifying that the attachment belongs to the requested issue. Responses are size-bounded and restricted to safe image MIME types. Authenticated Jira redirects are refused.

## Before publishing logs

Redact usernames, email addresses, local paths, Jira site names, issue content, session IDs, repository URLs, and tokens. Treat Jira descriptions and comments as untrusted content.
