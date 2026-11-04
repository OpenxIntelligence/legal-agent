# Legal Agent: three-minute walkthrough

Start `./legal-agent --demo` and open the printed local URL. Use fictional information;
changes persist locally, and no model API calls are made.

1. **Give it work (40 seconds).** Enter a fictional legal question, optionally attach
   a text record, and choose **Prepare investigation**. Show the saved assignment,
   missing context, proposed checklist, and document source. Explain that preparation
   is rule-based; no AI analysis has occurred in this demo.
2. **Explain live investigation (20 seconds).** In live mode the explicit start action
   runs the agent on this matter's question and evidence. Real plans, activity,
   requests, findings, and gaps refresh here. Directions to an active or paused run
   queue for the next resume; saved is distinct from applied.
3. **Inspect a completed example (50 seconds).** Return home and choose **Review a
   claim and its evidence**. Select a claim and inspect its source inline. Point out
   unavailable or unassessed support: a quotation match is not legal correctness.
4. **Correct and challenge (50 seconds).** Revise a claim, run its checker, and raise
   an objection or add a note. Record changes requested or a deliberate review
   decision. A changed draft requires renewed review.
5. **Leave a reviewable result (20 seconds).** Export the versioned review report.
   Return home to reopen the saved investigation; its assignment and follow-up
   conversation persist. Its readable assignment/findings export is Markdown.

These are synthetic workflow demonstrations, not a legal-quality benchmark. A live
investigation requires configured model dependencies/credentials. No public repository
or deployment has been created; inherited licensing/history/private-data review remains
separate release work.
