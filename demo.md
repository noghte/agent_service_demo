This walkthrough follows the saved notebook run: **14 response drafts, one ticket for human review, and 15 saved decisions.**

1. **Prepare the run.** The notebook selects the local ticket service and the model that will write the responses.

2. **Load tickets and rules.** It reads the first 15 tickets and four policies. These policies allow drafting and human escalation, and set limits on when tickets may be closed.

3. **Build the context.** Each ticket is accompanied by its redacted details, relevant facts, policies, and a summary of earlier outcomes in its category. The first ticket's preview shows empty memory because there are no earlier saved outcomes in that category yet.

4. **Check what is allowed.** The first ticket, TKT-100000, is given a seven-day inactivity scenario. One policy allows closure after five business days; another requires General Inquiry tickets to remain open for ten. The stricter restriction blocks closure and sends the ticket toward human review.

5. **Ask the model for an allowed proposal.** The model writes a response draft for each of the other 14 tickets and an internal review note for the blocked ticket. The workflow checks each proposal against the rules again before accepting it.

6. **Review and collect the results.** The saved output confirms **14 drafted responses and one human-review item**. The review note explains the conflicting closure rules. Results and the review queue are saved locally. These are simulated actions: drafts are not sent, and the queue does not notify a person.

7. **Keep state.** The notebook confirms **15 saved decisions**. State records progress and outcomes so the workflow can pick up later. Each ticket also has its own separate conversation history.

8. **Demonstrate reuse on a rerun.** All saved rows show `Cached=False`: these results were newly generated. Rerunning the unchanged batch should show `Cached=True`, reusing saved decisions without asking the model again. Changes to a ticket, policies, or model require a fresh result.

9. **Use memory across tickets.** Memory summarizes previous outcomes for tickets in the same category and adds that summary to later context. It comes from saved decisions, so it survives restarting the notebook. A saved draft counts as a draft, not a resolved issue; memory cannot override policy.

10. **Verify the audit trail.** The saved run contains **45 audit records**. The original record passes verification, while a deliberately altered copy fails. The audit provides evidence of the workflow's actions; it is separate from the memory used to write responses.
