---
name: refactor-for-testability
description: Use when about to change code that cannot be tested in its current shape — tight coupling to concretes, hidden side effects, ambient state (DateTime.Now, global config), or no seams to substitute dependencies. Triggers when the user asks to fix, update, or extend code and you discover it has no existing tests AND cannot easily get one. Also use when the user explicitly says "refactor this so it's testable" or "I want to add tests but the code is in the way". Not for code that already has tests, or for code where adding a test is straightforward.
---

# Refactor for Testability

You hit a change that the test-first rule cannot honour as-is: the code is in the way of being tested. This skill walks you through the safe sequence so you do not silently widen scope or skip tests.

## The sequence

The order matters. Doing these out of order is how refactors break things.

1. **Think twice before refactoring.**
2. **Ask user permission with a scoped proposal.**
3. **Draft the test plan first** (what you will assert, not how).
4. **Refactor — minimum surface area to make the seams testable.**
5. **Write the tests as drafted.**
6. **Then make the original change** the user came in for.

## Step 1 — Think twice

Before you propose a refactor, pressure-test whether you actually need one:

- Is there a smaller seam? A wrapper function, an extracted method, a single dependency to inject — instead of restructuring a class.
- Can you test through an integration boundary instead (HTTP, message, DB) without touching the unit's shape?
- Is the change really critical? A typo fix, a string update, a new log line may not warrant refactoring legacy code.
- Is the file changing soon for other reasons? If a bigger rewrite is imminent, fold this into that work rather than pre-paying.

If a smaller path exists, take it. Refactoring is not free — it adds risk to a change the user did not ask for.

## Step 2 — Ask permission

If the refactor is genuinely needed, **stop and ask the user before touching anything**. Frame the request so the user can decide quickly:

- **What you want to change**, in one sentence (e.g., "extract `OrderProcessor.Charge` to take an `IPaymentClient` instead of constructing `StripeClient` inline").
- **Why** — the specific testability blocker (e.g., "currently it news up `StripeClient` so we cannot substitute it in a test").
- **Blast radius** — files touched, public API impact, callers affected.
- **The alternative** — what happens if we do not refactor (typically: ship the change without a test, or skip the change).

Wait for explicit approval. Do not assume "go ahead with the fix" includes "and refactor the surrounding code."

## Step 3 — Draft the test plan

Once approved, **write the test cases down before you change any code.** A short list is fine — name and intent, not implementation:

```
- charges_card_when_order_is_valid: happy path, returns success
- returns_failure_when_payment_client_throws: error propagation
- does_not_charge_when_order_is_already_paid: idempotency
- writes_audit_log_with_correct_order_id: side-effect contract
```

This plan does two jobs:

- It forces you to define the contract the refactor must preserve.
- It gives the user something concrete to challenge before you spend time on code.

If the test plan reveals behavior you do not understand, **stop and read the code or ask** — do not refactor code whose behavior you have not pinned down.

## Step 4 — Refactor

Now change the code. Stay disciplined:

- **Behavior-preserving changes only** in the refactor commit. Extract, inject, rename. Do not fix the bug yet, do not add the feature yet.
- **Smallest seam that unblocks the tests.** Resist the urge to "clean up while you are here."
- Run the existing test suite after the refactor — the tests that *did* exist must still pass.

## Step 5 — Write the tests

Implement the test cases from the plan. They should pass against the refactored code without further changes (because the refactor was behavior-preserving). If a test fails, the refactor was not actually behavior-preserving — fix that before moving on.

## Step 6 — Make the original change

Now do what the user originally asked for. With tests in place, you have a safety net: each subsequent edit can be verified against the contract you just locked down.

Add or update tests for the new behavior in the same commit as the change.

## Anti-patterns to avoid

- **Refactor + feature in one commit.** Hides the risky parts of both. Always separate.
- **"I'll add tests after the refactor lands."** No. The tests are the whole point of the refactor.
- **Widening scope mid-refactor.** If you discover a second testability problem while refactoring the first, stop and ask before pulling it in.
- **Mocking everything to force a test through unchanged code.** If the test reads like a script of internal calls, the seam is in the wrong place — fix the seam.

## When to skip this skill

- Code already has tests and adding one more is straightforward → just write the test.
- The change is to a pure function or already-injected dependency → no refactor needed.
- The change is in code with a planned imminent rewrite → fold into that work.
