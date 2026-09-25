# Bridging into refactor-for-testability

If three rounds of adding tests have not got the uncovered diff under 20%, the problem is rarely "we need more tests." It's that the new code can't be tested cleanly in its current shape. At that point, switch from *more tests* to *better seams*.

## When to switch

You've completed three add-tests rounds. After each:

- The uncovered % barely budged, OR
- The tests you wrote are awkward (heavy mocks, lots of setup, brittle assertions, sleeping for time, hitting the network just to check a calculation).

Awkward tests are the symptom; bad seams are the cause. Don't push through with more awkward tests — that's how test suites become a tax everyone resents.

## How to hand off to `refactor-for-testability`

The `refactor-for-testability` skill exists for exactly this transition. To use it well, give it a focused brief, not the whole diff:

1. Pick **one specific uncovered block** that resisted testing in rounds 1–3.
2. Identify *why* it was hard to test. Common shapes:
   - A method that constructs its own dependencies (`new HttpClient()` inside).
   - Reads ambient state (`DateTime.UtcNow`, `Environment.GetEnvironmentVariable`, static singletons).
   - Side effects mixed with pure logic in the same method.
   - A god class — too many responsibilities, can't isolate one.
3. Invoke the skill with that brief, naming the file, the method, and the testability problem.

Example brief to feed the refactor skill:

> `PartnerLedHandler.HandleAsync` (file: `src/Contracts.Api/PartnerLedHandler.cs`, lines 78–140) constructs `new HttpClient()` and reads `DateTime.UtcNow` directly. I tried three rounds of adding tests but each test ended up either spinning up a real HTTP listener or wrapping `DateTime` calls. Apply the testability refactor for "ambient state at the edges, pure logic in the middle" — extract the time and HTTP client to injected dependencies.

## After the refactor

Run `measure.sh` again and add the test that was previously impossible. The point of the refactor is to enable a test you couldn't write before — *it must end with that test landing*. A refactor without the unblocked test is just churn.

## Two refactor rounds, then stop

If two rounds of refactor-then-test still haven't got the metric under 20%, do not keep going. The shape of the problem is bigger than this PR. **Stop and hand back to the user** with:

- The current uncovered % and which file/lines are still uncovered.
- What was tried (rounds 1–3 add-tests + 2 refactor rounds).
- A short recommendation:
  - Split the PR — land the testable parts now, isolate the hard part for a follow-up.
  - Accept the gap with rationale (e.g., "this is a thin adapter over a third-party SDK; cover at integration level instead").
  - Bigger redesign needed — out of scope for a coverage gate.

The user decides. This skill is a quality gate, not a hard wall — it earns its keep by surfacing the right question, not by blocking the user with an arbitrary threshold.
