## Summary
Restructure Gemini CLI GitHub workflows with documentation/templates and remove obsolete CI workflows.

## Changes
- Move Gemini workflow YAMLs into categorized subdirectories with new README/TOML templates for dispatch, PR review, triage, and assistant flows.
- Add Gemini CLI workflow documentation (`README.md`, `CONFIGURATION.md`, `AWESOME.md`) plus a GitHub App manifest.
- Remove legacy `build-test.yml`, `shellcheck.yml`, and `gemini-auto-review.yml` workflows.
- Simplify the Claude code review workflow prompt by removing redundant header lines.

## Testing
- Not run (workflow/doc-only changes).

## Checklist
- [ ] Code follows project style guidelines
- [ ] Tests pass locally
- [ ] Documentation updated if needed
- [ ] Breaking changes documented (if applicable)
