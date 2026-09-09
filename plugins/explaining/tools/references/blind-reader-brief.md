# Blind-reader brief (template)

Render the region between the two markers below by replacing each slot with its value, and
send that rendered text as the subagent's entire brief. Change nothing else in the region:
the eval check asserts every non-slot line survives verbatim, and the rendered text is what
the review log records.

<!-- BRIEF-START -->
Read the file at {{artifact_path}}. It was written for {{audience}}, in {{language}}.

You are a first-time reader. You have no other context, and you must not go looking for any:
do not read other files, do not search anywhere, do not guess at what was intended. Judge
only what is on the page.

Report every place you could not follow, in the order they appear. Give each one as three
labelled lines:

LOCATION: a short quoted phrase, or the heading it sits under
WHAT BROKE: one sentence, in your own words
SEVERITY: BLOCK if you could not understand it, NIT if you understood it but it read rough

Look especially for: a term used before it is introduced, a jump between two ideas with no
bridge, a claim with nothing concrete to anchor it, a sentence you had to read twice, and a
section whose purpose is never stated.

Report the single hardest passage even when nothing blocked you, as a NIT.

End your reply with exactly one terminal line, and nothing after it: READER: PASS when you
found zero BLOCK findings, or READER: FAIL n BLOCK when you found n of them.
<!-- BRIEF-END -->

## Rendering notes (never send these to the reader)

- The three slots are the only values that may cross into the brief: `artifact_path` is the
  path of the file on disk, `audience` is one short phrase, `language` is the language
  the file is written in. Nothing else crosses — not the user's request, not your sources, not
  your reasoning, not the draft text pasted inline, and not any earlier round's findings. A
  reader that has been told what the draft was supposed to say can no longer tell you what it
  actually says.
- Reader model: `sonnet` by default, the same tier the tribe's reviewer agent uses. This is the
  knob to turn when a draft is unusually long or unusually cheap to read. If the dispatch tool
  in this session does not accept a model override, dispatch with the session default and record
  the model actually used in the review log.
- Dispatch a fresh subagent every round. Never a fork of the current session, and never the
  current session itself: the whole value is a context that has never seen the draft before.
- Dispatch the reader with `run_in_background: false`, and wait for its reply. This skill runs
  in headless sessions, where a backgrounded reader never returns a verdict and the round's log
  record can never be completed.
- Open the round's log record before you dispatch: the round number and this rendered brief,
  verbatim. Complete it with the findings, block count, verdict and your action when the reader
  returns. A round with no opened record is a round whose reader was never dispatched.
