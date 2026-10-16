# Security policy

Please do not report security issues in public issues. Use GitHub's private vulnerability
reporting (Security tab → Report a vulnerability) instead.

contractctl generates SQL that sets access controls (grants, column masks and row filters).
A bug that drops or weakens one of them in the generated SQL is treated as a security issue.
