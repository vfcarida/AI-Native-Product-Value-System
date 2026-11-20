# Spec-Kit Patterns Guide (EVO-020)

The `product_value_system.spec_kit` package brings GitHub Spec-Kit structured templates, automated completeness scoring (0–100), spec-to-code/test traceability, and Customer Discovery insight conversion into the Product Value System.

---

## Available Templates

| Template Type (`--type`) | ID Prefix | Core Elements |
| :--- | :---: | :--- |
| `user-story` | `US-` | `As a / I want / So that`, 3+ Given/When/Then acceptance criteria, Scenarios, NFRs, Out of Scope, Related |
| `scenario` | `SCENARIO-` | Gherkin Given/When/Then blocks, Data Tables, State Transitions, Edge Cases |
| `api-contract` | `API-` | Auth, Request/Response JSON schemas, Error responses (`400`/`401`/`403`), cURL examples, Rate Limiting & Idempotency |
| `nfr` | `NFR-` | Measurable quantitative latency/availability/security targets + verification checklist |
| `decision` | `DEC-` | Lightweight decision record linking to full [ADRs](adr-guide.md) |

---

## CLI Workflow

```bash
# 1. Generate a new Spec-Kit user story, scenario, or API contract
pvs spec new user-story --title "Biometric Transfer" --id US-042 --role customer
pvs spec new scenario --title "PIX Transfer Validation" --id SCENARIO-015
pvs spec new api-contract --title "Create Transfer" --method POST --path transfers

# 2. Validate completeness (0-100 score) on a single file or entire directory
pvs spec validate docs/specs/US-042-biometric-transfer.md --min-score 80
pvs spec validate docs/specs/

# 3. Trace spec IDs to source code and test files (via inline `# See US-042` comments)
pvs spec trace US-042

# 4. Generate traceability coverage report (text or JSON)
pvs spec coverage docs/specs/
pvs spec coverage docs/specs/ --format json

# 5. Export consolidated Markdown specification catalog
pvs spec export docs/specs/ --output docs/specs/CATALOG.md
```

---

## Converting Customer Discovery Insights into User Stories

Use `DiscoverySpecGenerator` to turn raw customer insights into structured Given/When/Then User Story drafts ready for validation:

```python
from product_value_system.spec_kit import DiscoverySpecGenerator, SpecValidator

generator = DiscoverySpecGenerator(llm_callable=my_llm)  # or llm_callable=None for deterministic mode
story_md = await generator.generate_user_story_from_insight(
    {
        "pain_point": "Manual biometric retries time out and clear form fields",
        "job_to_be_done": "Complete high-value transfers in under 5 seconds with safe retry",
        "user_role": "mobile banking customer",
    },
    story_number="042",
)

validation = SpecValidator().validate_user_story(story_md)
assert validation.valid and validation.score >= 90
```

---

## IDE Snippets (VS Code)

Workspace snippets are pre-installed in [`.vscode/spec-kit.code-snippets`](../.vscode/spec-kit.code-snippets). Type any of these prefixes in a Markdown file:

- `pvs-user-story`
- `pvs-scenario`
- `pvs-api-contract`
- `pvs-nfr`
- `pvs-decision`

---

## Related Documentation

- [Architecture Decision Records Guide](adr-guide.md)
- [Automated Drift Detection Guide](drift-detection.md)
- [Multi-Agent Independent Review Guide](review-guide.md)
