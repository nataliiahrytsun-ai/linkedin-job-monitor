# Zoho Recruit public career-site spike

## Decision

**Production adapter approved for the verified embedded-HTML contract.**

This is a platform-level `ZohoRecruitSourceAdapter`, not a BGTS-specific
integration. It uses one ordinary public HTTPS request and does not use the
authenticated Zoho Recruit REST API, OAuth, candidate login, cookies, browser
automation, CAPTCHA handling, stealth, proxies, or private endpoints.

## Public transport

The audited BGTS page at `https://jobs.bgts.com/jobs/Careers` is a custom-domain
Zoho Recruit career site. Its server-rendered HTML contains:

- Zoho Recruit assets below `https://static.zohocdn.com/recruit/`;
- the career-site root `#career-website-main`;
- a `#jobs` hidden input containing the published jobs JSON array;
- `#meta`, `#pageJson`, and `#moduleMeta` hidden JSON inputs describing the
  public career page, enabled jobs block, and `Job_Openings` module.

The saved diagnostic contained 24 jobs. Each item exposed a stable Zoho record
`id`, public title, description, location fields, job type, industry, opened
date, remote flag, and publication flag. A public detail request succeeds at
`/jobs/<page>/<record-id>` without requiring a title slug, so the adapter does
not have to generate or guess URL text.

An RSS route was visible and returned HTTP 200, but it was not selected as the
adapter transport because its completeness and tenant configuration could not
be proven from the public page. The embedded snapshot is the data used by the
career page itself.

## Stability and completeness boundary

Zoho's official career-site documentation describes the career page as the
published job listing and documents a maximum of 750 displayed jobs. The
adapter therefore accepts an embedded snapshot only when all platform
signatures and JSON contracts agree and the job count is below 750. A count of
750 or more is treated as potentially capped and fails closed.

References:

- https://help.zoho.com/portal/en/kb/recruit/talent-sourcing/career-site/articles/customize-job-templates
- https://help.zoho.com/portal/en/kb/recruit/talent-sourcing/career-site/articles/embed-jobs-on-your-website
- https://help.zoho.com/portal/en/kb/recruit/self-service-portal/setting-up-domain/articles/setting-up-your-domain

The adapter also fails closed on visible pagination, missing or duplicate
payloads, malformed JSON, missing platform signatures, invalid metadata URLs,
missing enabled jobs layout, missing `Job_Openings` metadata, empty jobs,
non-published entries, invalid or duplicate IDs, and invalid required titles.
No partial `SourceBatch` is returned.

## Discovery

Default `*.zohorecruit.com/jobs/<page>` URLs are detected by their public host
and route. Custom domains require the `/jobs/<page>` route plus all independent
Zoho Recruit HTML signatures; company names or BGTS-specific IDs are not used.
Supported detections use the `zoho_recruit` registry key and are ineligible for
Generic fallback.
