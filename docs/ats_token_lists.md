# Seeding ATS company tokens (Phase 3 / Phase 9)

Tier C (Greenhouse / Lever / Ashby / SmartRecruiters) has **no global search** — coverage =
the quality of your per-company token list. This doc is how to build and curate it.

## How to find a company's token/slug
- **Greenhouse**: `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`.
  The `{token}` is the slug in `boards.greenhouse.io/{token}` (e.g. `stripe`).
- **Lever**: `https://api.lever.co/v0/postings/{slug}?mode=json`. `{slug}` is in
  `jobs.lever.co/{slug}`.
- **Ashby**: `https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true`.
  `{org}` is **case-sensitive** (e.g. `Notion`).
- **SmartRecruiters**: `https://api.smartrecruiters.com/v1/companies/{id}/postings?country=in`.

## Bulk seed
`Feashliaa/job-board-aggregator` on GitHub ships a ~95k-slug dataset (CC BY-NC, fine for
personal use). Curate down to India-friendly + remote-first + your target companies.

## Curated starter list (fill in and paste into the dashboard config)
- greenhouse.tokens: [ ... ]
- lever.slugs:       [ ... ]
- ashby.orgs:        [ ... ]
- smartrecruiters.company_ids: [ ... ]

> Tip: pick companies that actually hire India-remote interns in backend/systems/ML.
