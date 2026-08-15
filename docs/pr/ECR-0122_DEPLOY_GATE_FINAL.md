# ECR-0122 — The Deploy Gate (final)

**Status:** Proposed
**From:** claude.ai (spec author), finalized on Claude Code's §0 answers verified against
`main @bfed359` (CI run 31301879849 green, headSha verified)
**Date:** 2026-08-07
**Number:** 0122 per Claude Code's repo read — **re-read `ECR-LOG.md` at build time.** This
line has already shipped one ECR missing its index row on a stale green; the deploy ECR is
the worst place to repeat that.

**What this ECR does:** moves the customer flow from proven-in-repo to running-on-the-box,
as one atomic gate. Partial application is the only failure mode that matters — a
customer-reachable endpoint missing one protection.

---

## 1. Preconditions discharged (answers of record)

- **Sessions are durable and cross-worker.** `PostgresSessionStore` landed in ECR-0120 with
  a cross-instance witness; ECR-0123 closed the evidence/finding half. On
  `backend=postgres` every store the portal touches is durable: accounts, invites, sessions,
  consent, audit, objects, evidence, findings — with one transaction per write unit
  (ECR-0124) and opaque DB-resolved cookies, so **no sticky sessions and no single-worker
  pin**. In-memory remnants on the postgres path (blob store, auxiliary engine stores) are
  disclosed in ECR-0123's LOG entry and touched by no portal flow.
- **Independent CI green on `main`** at exactly `bfed359` — the gate's CI obligation,
  discharged. Recorded as the first main-green in the 0115–0125 line's history: earlier
  merges rode a misread gate (runs existed and died at an unpinned-ruff format check, fixed
  and pinned in #325).
- **Audit tamper-evidence: open** — ECR-0124 bought atomicity, not a hash chain;
  `aq_audit_event` has no `prev_hash`/`record_hash`, and append-only remains enforced by
  omission.
- **General `_error` no-oracle guard: open** — ECR-0125's byte-comparing route census
  structurally catches the id-echoing 404 on every object-addressed route (the specific
  regression ECR-0119 feared), but nothing asserts `_error` never reflects request content
  on any route or status.
- **No mobile collector exists and none ever ran.** Collections are host-based only: Linux
  `.pyz` zipapp and Windows PowerShell, both validated on real machines. Any "tested from
  mobiles" impression was the dashboard being viewed in a mobile browser.

## 2. Gate requirements — all together, or not at all

**R1 — PostgreSQL on the box**, loopback, `AQELYN_DATABASE_URL` delivered via systemd
environment to the portal service only. **Backup and restore-verification run against the
live database before the portal accepts its first account.** Repo-proven and box-proven are
different facts.

**R2 — Replace the :8800 stdlib script with the repo `PortalApplication`** behind a real
body-reading HTTP server. The old script is retired only after R7 passes.

**R3 — At-socket size enforcement.** The server refuses oversized bodies **before**
buffering: a streaming read bounded by one named module constant, shared with `handle()`'s
existing check so the two cannot drift (defence in depth, single source). `Content-Length`
is an early reject when present, never trusted as sufficient — a body that lies about or
omits its length is bounded by the streaming read. **Acceptance: process memory does not
rise to the body's size on an oversized upload — measured, not asserted.** Until this
exists, "≤ 1 MiB" is a policy, not a protection: an authenticated client can exhaust a 4 GB
box.

**R4 — Two-worker smoke.** Multi-worker is *supported* (§1) but has never been *exercised*;
those are different risks and only the first is cheap to close. Run login → upload →
read-back across two workers, proving a session minted on one resolves on the other and an
upload through one is readable through the other. Cheap and decisive; if it fails, the
deploy runs single-worker and the failure is a recorded finding, not a silent pin.

**R5 — nginx `limit_req` on `/login`, `/register`, `/scans`.** Placed at nginx by design and
non-existent until configured. Gate element, not follow-up.

**R6 — The two open obligations land in this gate, not after it:**

- **Audit hash chain** — `prev_hash`/`record_hash` over the append-only log, verifiable
  end-to-end. **Reason it belongs before cutover, not after:** built now, the chain starts
  at a genuine genesis with nothing to backfill; built after, it requires either a chain
  break at the cutover boundary or a migration over live customer audit records — and the
  audit trail's value in any future dispute depends on it covering the whole history.
- **General `_error` no-oracle guard** — a structural assertion that neither `_error` helper
  (`portal/app.py`, `portal/server.py`) reflects request-supplied content on any route or
  status, mutation-proven (inject an echo → guard RED). ECR-0125 pinned the byte-equality of
  three 404s; this pins the property they depend on.

**R7 — Cutover proof on the real box.** The ECR-0119 adversarial isolation matrix and the
ECR-0118 upload attack list re-run **against the deployed portal**, not the repo, plus R3's
memory measurement and R4's smoke. This arc's review standard, applied at the deploy
boundary.

**R8 — Owner-gated go.** R7 passing is the technical bar; the owner says go. This is the
first irreversible step — real accounts, real scans persisting.

**R9 — Coverage honesty in customer-facing copy.** `aqelyn.com/scan` is already public. Its
copy, the portal UI, and any plan or pricing text must state coverage as it is: **Linux and
Windows hosts are scanned; any device can view the dashboard.** No mobile coverage is
implied anywhere, and the existing honest line ("not possible from a host collector") is
preserved. Checked at the gate because a false coverage claim becomes a
misrepresentation the moment money is involved — and this is the last moment before it is.

## 3. First cutover scope

**Owner-only.** Accounts created by hand; no public registration path enabled. Collect from
the assets already tested — Linux servers and Windows laptops (not mobiles, per §1 and R9).
This proves the deployed flow end-to-end with real data while nobody else can reach it, and
running it yourself for a period is the cheapest way to surface operational gaps no test
suite shows.

## 4. Out of scope — the paid self-service arc

Public registration, email verification, payment, subscription state, plan gating, and the
tax/consumer-law obligations are a separate arc, opened after the cutover proves the flow.
Its first question is the owner's and shapes everything below it: **what a plan includes —
asset count, scan frequency, retention period** — because the subscription state machine is
built against that answer, including the failure cases (expired, failed renewal, refunded,
charged back) and what each means for a customer's already-stored scans.

## 5. Ball

**Next: Codex implements** ECR-0122 (re-checking the number first) — R1–R6 as one change
set. **Then: Claude Code** runs R7 on the box and reports. **Then: the owner says go**
(R8), the old script retires, and the paid arc opens with the plan-contents question.
