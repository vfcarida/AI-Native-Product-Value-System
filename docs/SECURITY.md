# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

**DO NOT** open a public issue for security vulnerabilities.

For this reference implementation, open a private GitHub Security Advisory at:
`https://github.com/<org>/<repo>/security/advisories/new`

Include:
- Description of vulnerability
- Steps to reproduce
- Impact assessment
- Suggested fix (if any)

**Response Timeline**:
- Acknowledgment: Within 48 hours
- Initial assessment: Within 1 week
- Fix target: Within 4 weeks (critical), 8 weeks (high)

## Security Considerations

**This is a reference implementation for educational purposes.**

- ✅ Suitable for: Research, education, local development
- ❌ NOT suitable for: Production, processing real PII, autonomous deployment

**Known Limitations**:
- Single-machine deployment (no HA, DR)
- Docker isolation (not microVMs)
- Regex-based secret detection (not ML)
- No global rate limiting
- No formal security audit

See [THREAT_MODEL.md](THREAT_MODEL.md) for detailed threat analysis.

## Security Best Practices

If adapting this system for production:

1. **Add formal security audit** (external penetration test)
2. **Implement model gateway** (rate limiting, caching, quota)
3. **Use microVMs** (Firecracker, gVisor) instead of Docker
4. **Add ML-based secret detection** (detect-secrets, Talisman)
5. **Implement WAF** (Web Application Firewall) if exposing API
6. **Add dependency scanning** (Dependabot, Snyk)
7. **Implement SIEM** (Security Information and Event Management)
8. **Add incident response plan** (on-call, escalation)
9. **Conduct tabletop exercises** (simulate incidents)
10. **Implement zero-trust architecture** (mutual TLS, service mesh)

## Secure Development

All contributors MUST:
- Run `pytest tests/security/` before committing
- Never commit secrets (use pre-commit hooks)
- Review Cedar policies for logic errors (`make validate-policies`)
- Follow principle of least privilege

---

**Document Version**: 1.0.0  
**Last Updated**: 2026-11-18
