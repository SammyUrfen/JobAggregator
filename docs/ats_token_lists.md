# ATS token lists — why they exist and how to seed them

Tier C sources (Greenhouse / Lever / Ashby / SmartRecruiters) are **company-scoped ATS APIs**,
not job boards. There is **no global search endpoint** — you cannot ask "give me all remote
backend roles." You can only ask "give me every open posting *at company X*," one company at a
time. So for these four sources:

> **Coverage is exactly the quality and length of your per-company token list.**
> A company you never list is a company you never see. Curating this list *is* the work.

Everything else in the pipeline (dedup, salary→INR/month, keyword/remote filters) still runs on
what these sources return, but it can only filter jobs that were fetched, and a job is only
fetched if its company's token is in your list.

## Why each company is now stale-isolated (the important design point)

Each ATS posting is tagged with a **per-company source string**, not a per-provider one. In the
code:

- Greenhouse → `source = f"greenhouse_{token}"` (`sources/ats_greenhouse.py`)
- Lever → `source = f"lever_{slug}"`
- Ashby → `source = f"ashby_{org}"`
- SmartRecruiters → `source = f"smartrecruiters_{company_id}"`

That granularity matters because of the **per-source success guard** in stale-deletion
(`pipeline/stale.py`, PLAN §4.5). `expire_stale` only iterates over `succeeded_sources` — a
source that failed this cycle is *never* touched, so none of its jobs can be marked `stale` or
`deleted`. `run_ats` (`sources/base.py`) isolates failure **per company**: an empty result is
legitimate (no openings), but a `SourceError` marks only *that* company failed and emits one
`sub_results` entry per company.

Concrete consequence: if you list 40 Greenhouse tokens and `stripe` returns HTTP 200 while
`gopuff` 404s (its board was renamed/removed), `stripe`'s stale jobs expire normally and
**`gopuff`'s existing jobs are left exactly as they were** — because "we couldn't see it" is not
the same as "it disappeared." One dead token can no longer wrongly wipe another company's live
postings. That is the whole reason the source tag is per-company and not just `"greenhouse"`.

## Where the lists live

Not in code, not in the DB — in the **dashboard `/config` page, under Sources**. They map to the
Pydantic schema (`config/schema.py`) exactly as:

```yaml
sources:
  ats:
    greenhouse:      { enabled: true, tokens: [] }        # config.sources.ats.greenhouse.tokens
    lever:           { enabled: true, slugs: [] }         # config.sources.ats.lever.slugs
    ashby:           { enabled: true, orgs: [] }          # config.sources.ats.ashby.orgs
    smartrecruiters: { enabled: true, company_ids: [] }   # config.sources.ats.smartrecruiters.company_ids
```

Edits save through the schema validator and apply on the **next run** (config is never hot-read
mid-run). `enabled: false` skips the whole provider regardless of list contents.

## Verify a token by hand — no auth required

All four endpoints are public GETs (no key, no cookie). These are the **exact URL patterns the
source modules hit**, so if a curl works, the aggregator will fetch it. Replace the placeholder
and look at the HTTP status: a valid token returns **200 with real postings**; a bad one returns
**404** (or a `{"ok": false}` body for Lever).

**Greenhouse** — `<token>` is the slug in `boards.greenhouse.io/<token>`:
```bash
curl -s 'https://boards-api.greenhouse.io/v1/boards/stripe/jobs?content=true' | head -c 400
```

**Lever** — `<slug>` is the slug in `jobs.lever.co/<slug>`. Returns a **bare JSON array** of
postings on success; a bad slug returns the object `{"ok": false}` (with an HTTP 4xx):
```bash
curl -s 'https://api.lever.co/v0/postings/netflix?mode=json' | head -c 400
```

**Ashby** — `<Org>` is **CASE-SENSITIVE**; match the capitalization of the public board
(`jobs.ashbyhq.com/<Org>`). `includeCompensation=true` is what surfaces salary when the company
publishes it:
```bash
curl -s 'https://api.ashbyhq.com/posting-api/job-board/Ramp?includeCompensation=true' | head -c 400
```

**SmartRecruiters** — `<CompanyId>` is the identifier in `careers.smartrecruiters.com/<CompanyId>`;
`?country=in` narrows to India-posted roles:
```bash
curl -s 'https://api.smartrecruiters.com/v1/companies/Stripe/postings?country=in' | head -c 400
```

### One-shot verify snippet

Paste a whitespace-separated list per provider; prints the HTTP code so 200 vs 404 is obvious.

```bash
verify_gh() { for t in "$@"; do printf 'GH %-20s %s\n' "$t" "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "https://boards-api.greenhouse.io/v1/boards/$t/jobs?content=true")"; done; }
verify_lv() { for s in "$@"; do printf 'LV %-20s %s\n' "$s" "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "https://api.lever.co/v0/postings/$s?mode=json")"; done; }
verify_ab() { for o in "$@"; do printf 'AB %-20s %s\n' "$o" "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "https://api.ashbyhq.com/posting-api/job-board/$o?includeCompensation=true")"; done; }
verify_sr() { for c in "$@"; do printf 'SR %-20s %s\n' "$c" "$(curl -s -m 20 -o /dev/null -w '%{http_code}' "https://api.smartrecruiters.com/v1/companies/$c/postings?country=in")"; done; }

# examples:
verify_gh stripe gitlab databricks
verify_ab Ramp Notion Linear
```

## Confirmed-live tokens (verified 2026-07-16)

These returned **HTTP 200** with real postings when probed directly against the endpoints above.
"Live" means the board exists and responds — it does **not** guarantee an India/remote role in
your target keywords on any given day. Anything tagged **candidate — verify** was not confirmed
to have India-remote backend/systems/ML openings; check the board before committing it.

**Greenhouse (`tokens`)** — 200: `stripe`, `gitlab`, `databricks`, `dropbox`, `coinbase`,
`postman`, `airbnb`.
Notably **404** (do not add): `razorpay`, `gopuff` — not (or no longer) on Greenhouse.

**Lever (`slugs`)** — 200: `netflix`, `plaid`.
**404** (do not add): `brex`, `ramp` — moved off Lever (Ramp is now on Ashby), `leapwallet`.

**Ashby (`orgs`, case-sensitive)** — 200: `Ramp`, `Notion`, `Linear`, `Vercel`, `OpenAI`,
`Cohere`.

**SmartRecruiters (`company_ids`)** — 200 with `?country=in`: `Stripe`, `Square`, `Instacart`.

> India/remote suitability of the above is **candidate — verify** in every case: these are
> global-brand boards confirmed reachable, not confirmed to be hiring India-remote in your
> niche today. Curate toward companies that actually post India-remote backend/systems/ML/LLM
> roles (e.g. India-HQ startups and remote-first infra companies) rather than padding the list
> with big names.

## Practical seeding workflow

1. Start from companies you'd actually take a role at that hire India-remote in backend /
   systems / distributed / ML / LLM / RL.
2. Find each one's public board URL (`boards.greenhouse.io/…`, `jobs.lever.co/…`,
   `jobs.ashbyhq.com/…`, `careers.smartrecruiters.com/…`) and lift the token from the path.
3. Verify with the curl / snippet above — keep only 200s. Mind Ashby's case sensitivity.
4. Paste into `/config` under the matching field, save, and run once. Because tokens are
   stale-isolated, adding a wrong/dead token is low-risk: it fails in isolation and touches
   nothing else.
5. Re-check periodically — companies migrate ATS providers (this is why `razorpay`, `ramp`, and
   `gopuff` fell out of the lists above); a token that 404s just quietly contributes nothing.

## Limitations

- **Token quality is a manual, perishable input.** There is no discovery here — the aggregator
  cannot find companies you didn't list, and boards move between ATS vendors, so a curated list
  decays. Budget for occasional re-verification.
- **"Live" ≠ "relevant."** A 200 only proves the board is reachable. Whether it currently has an
  India-remote role matching your keywords is decided later by the filter stage, per run.
- **The `?country=in` filter is SmartRecruiters-specific.** Greenhouse, Lever, and Ashby have no
  server-side India filter; India/remote scoping for those happens in the pipeline's normalize +
  filter stages, so you fetch the whole board and discard most of it.
- **Ashby case sensitivity is a real footgun.** A wrong-case org may resolve differently or fail;
  always copy the exact capitalization from the public board URL. (Some orgs happen to resolve in
  either case, but do not rely on it.)
- **HTTP codes above are point-in-time (2026-07-16).** A token confirmed today can 404 tomorrow
  if the company offboards or renames its board.
