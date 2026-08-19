# Generic fallback spike (consolidated)

## Goal

Add a conservative fallback path for eligible public career sources that are not
handled by existing deterministic adapters.

## Architecture

```
Discovery
-> classification
-> is_generic_fallback_eligible(...)
-> explicit source key: generic
-> source registry
-> GenericSourceAdapter
-> HTTP fetch
-> deterministic candidate extraction
-> OpenAIJobExtractionProvider
-> deterministic validation
-> SourceBatch
-> existing normalization/persistence/reconciliation pipeline
```

Existing production adapters (Lever, Darwinbox, JazzHR, DreamJobs) remain
unchanged and continue to be selected first for their source keys.

## What was validated

- Deterministic candidate extraction on synthetic and real-structure fixtures.
- URL provenance: LLM output never becomes authoritative for URLs.
- Provider boundary with strict structured schema and fail-closed validation.
- Discovery eligibility gate (`is_generic_fallback_eligible`) with conservative
  policy and explicit denials for non-public/noisy sources.
- Registry wiring for explicit internal `generic` source key.
- Manual discovery confirmation can map eligible unsupported candidates to
  `CompanySource(source="generic")`.
- Generic adapter fails closed for:
  - invalid/non-public source URLs
  - HTTP fetch errors
  - unsupported pagination/completeness risk
  - no deterministic candidates
  - provider configuration/response validation failures
  - empty validated job set

## Eligibility policy (single source of truth)

Implemented in `discovery.classification.is_generic_fallback_eligible`.

- `supported_ats`: NO
- `company_jobs_page`: YES (only when public/valid and not rejected/ignored)
- `unsupported_ats`: YES only with strong evidence
- `possible_job_source`: YES only with stronger evidence/confidence
- `external_job_board`: NO
- `not_a_job_source`: NO
- `uncertain`: NO
- rejected/ignored/non-public/login/privacy/legal/social/apply-only: NO

## Fail-closed policy

- No blanket unknown-source fallback in registry.
- No speculative browser automation for generic flow in this phase.
- Pagination indicators are treated as incomplete snapshot risk and fail closed.
- On adapter/provider failure, `SourceError` propagates to existing failed-run
  lifecycle; reconciliation is not executed for failed runs.

## Production behavior

- Supported sources: deterministic adapters only.
- Eligible generic sources: explicit `generic` source path only.
- Ineligible/unknown sources: safe non-execution path (review/unsupported/fail).

## Known limitations

- Not a universal parser for all career sites.
- No universal JS/browser transport in generic production path.
- No universal pagination traversal for arbitrary unknown platforms.
- Real-world coverage still depends on validation against the target company set.
- No live OpenAI validation is performed in automated tests.

## Correct claim

Generic fallback is a conservative supplemental path for eligible public career
sources. It does not replace platform-specific adapters, and real-world coverage
must still be validated on the actual company portfolio.
