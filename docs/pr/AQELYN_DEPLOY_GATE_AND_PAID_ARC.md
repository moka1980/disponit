# AQELYN — The Deploy Gate (ECR number to be verified), and the Paid Self-Service Arc

**Status:** Proposed
**From:** claude.ai (spec author), from Claude Code's arc-close report (`main @bfed359`,
CI run 31301879849 green, headSha verified)
**Owner decisions incorporated:** self-service paid access is the product direction
(overriding the earlier invite-only decision — see §4); the first cutover is owner-only
against already-tested assets.

⚠️ **Number:** the deploy gate has been called "ECR-0122" in conversation while 0123/0124/
0125 have shipped. **Re-read `ECR-LOG.md` and take the next genuinely free number.** This
line has already shipped one ECR missing its index row on a stale green; a reused or
skipped number in the deploy ECR is the worst place to repeat that class of error.

---

## 0. Preconditions — three answers needed before implementation starts

Asked of Claude Code; the gate cannot be finalized without them:

1. **Did ECR-0123's durable stores cover `Session`, or only evidence/findings?** This
   decides §2 R4: if sessions are still process-memory, the deploy is pinned to **one
   worker** (a session minted on worker A is invisible to worker B — login breaks silently,
   and no test on the box catches it until a customer does). If sessions are durable and
   cross-worker, R4 is dropped and multi-worker is available.
2. **Are the audit hash-chain and the general `_error` no-oracle guard still open, or did
   they land in ECR-0124/0125?** Both were listed as gate obligations; the byte-comparing
   route census (0125) may already discharge the second.
3. **Does a mobile collector exist?** The customer-flow brief put mobile on a separate track
   (device-management enrollment, signed profiles); the owner reports testing against
   mobiles. If collection was manual or partial, the product must not imply mobile coverage
   until a mobile collector ships.

## 1. What is already discharged

- **CI green on `main` at `bfed359`** — the independent-machine verification the entire
  0115–0125 line had been missing, and the pre-deploy CI obligation. Recorded as first-of-
  its-kind for this line, because the whole line merged on a misread gate until ruff==0.16.2
  was pinned.
- Codex's independent review of the arc: taken, both P1 reopens fixed, five hardening rounds
  survived, accepted.
- Carried mutation matrix at 146; backup restore-verified.

## 2. The gate — atomic, or not at all

The guarantees ECR-0115–0125 proved in the repo hold in production only if every element
below lands together. **Partial application is the single failure mode that matters**: a
customer-reachable endpoint missing one protection.

**R1 — PostgreSQL on the box**, loopback, with backup **and restore-verification** run
against the live database before the portal accepts its first account. Repo-proven and
box-proven are different facts.

**R2 — Replace the :8800 stdlib script with the repo `PortalApplication`** behind a real
body-reading HTTP server.

**R3 — At-socket size enforcement.** The server refuses oversized bodies **before**
buffering: streaming read bounded by one named module constant shared with `handle()`'s
existing check (defence in depth, no drift). `Content-Length` is an early reject when
present, never trusted as sufficient. Acceptance: process memory does not rise to the body's
size on an oversized upload — measured, not asserted. Until this exists, "≤ 1 MiB" is a
policy, not a protection, and an authenticated client can exhaust a 4 GB box.

**R4 — Worker count matches session durability** (per §0.1). If sessions are process-memory:
one worker, asserted in the deploy config, with the constraint recorded and its lifter
(durable sessions) named as the follow-up that removes the assertion in the same PR that
proves multi-worker works.

**R5 — nginx `limit_req` on `/login`, `/register`, `/scans`.** The rate limit was placed at
nginx by design and does not exist until configured. Gate element, not follow-up.

**R6 — Cutover proof on the real box:** the ECR-0119 adversarial isolation matrix and the
ECR-0118 upload attack list re-run **against the deployed portal**, not the repo. The old
script is retired only after this passes.

**R7 — Owner-gated go.** R6 passing is the technical bar; the owner says go, because this is
the first irreversible step — real accounts, real scans persisting.

**First cutover scope: owner-only.** Accounts created by hand (no public registration path
enabled), collecting from the assets already tested — laptops, servers, and mobiles subject
to §0.3. This proves the deployed flow end-to-end with real data while no one else can reach
it.

## 3. Immediately after the gate — hardening that pays for itself

- **Durable sessions**, if §0.1 says they are not yet (lifts R4's single-worker pin; the
  ECR-0116 shape — one test body, both backends).
- **Audit hash-chain**, if §0.2 says it is open: today's append-only is enforced by omission
  (no update/delete method, no DDL grant). Tamper-evidence becomes materially more valuable
  the moment paying customers exist and the audit trail is evidence in a dispute.
- **`_error` no-oracle guard**, if not discharged by 0125: the byte-equality proof depends on
  `_error` not echoing the request, so a future `_error` change could silently reintroduce
  the cross-tenant existence oracle.

## 4. The paid self-service arc — scoped separately, deliberately

The owner's direction: customers self-register, pay, and get access. This **replaces** the
invite-only decision taken earlier under delegation. It does **not** ride in the deploy gate,
for the gate's own reason — it adds a new hostile surface, and the gate's value is that
everything in it lands together.

Scope, in rough dependency order (each its own ECR when the arc opens):

1. **Public registration + email verification.** Open sign-up on a security product needs the
   abuse controls invite-only was standing in for: verification before access, rate limits,
   and a disable path.
2. **Payment integration.** Provider choice is an owner decision (a Norwegian-market-friendly
   option like Stripe with MVA handling, or a local alternative). The platform never handles
   card data; it holds a subscription state.
3. **Subscription state gates access** — and the interesting cases are the failure ones:
   expired, failed renewal, refunded, charged back. Each needs a defined behaviour for the
   customer's *existing stored scans* (retained and locked? deleted after a grace period?).
   This is a data-retention decision as much as a billing one, and it touches the
   delete-my-scans control already specced.
4. **Consumer-law and tax obligations.** Norwegian MVA on digital services, and the 14-day
   right of withdrawal for consumer sales, both bear on how the plan and refunds are
   structured. Worth an accountant's and possibly a lawyer's read before the pricing page
   exists — I can lay out the considerations, but I am not either.
5. **Pricing and plan model** — what a plan includes (asset count? scan frequency? retention
   period?), which is a product decision that shapes every ECR above it. **This one should be
   answered first**, because the subscription state machine is built against it.

**Recommendation on sequencing:** deploy gate → owner-only cutover proves the real flow →
then open the paid arc with the pricing/plan question, since everything in it depends on that
answer. Running the platform yourself for a short period first is also the cheapest way to
find the operational gaps that no test suite surfaces.

## 5. Ball

**Next: Claude Code** answers §0's three questions and confirms the free ECR number. **Then**
the gate is finalized and implemented, with R6 as the merge bar and R7 as the owner's go.
**Then** §3's hardening, and the paid arc opens with the pricing question.
