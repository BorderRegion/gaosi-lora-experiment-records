# Primary clean pairing: v2, published but inactive

The pending primary clean control must use its own fresh clean core followed by
512 specialization updates that inherit its phi and complete Adam state and
restart the episode index at zero. This pairs the mixed arm's non-noise routing
and stream seeds without adding another clean arm or changing the total budget.
See the original finding in `../clean_schedule_pairing_20261110_1407/`.

Use `organization_queue_corrected_v2.py` for the eventual controller handoff.
Its SHA256 is
`7e3b37b630891150dd6f939dc21dc52c5d4024e962d50c38151aa80cfa6e5724`.
The earlier preserved candidate assumed that `gaosi.meta` result.json contained
config; it does not. V2 checks the actual config sidecars, checkpoint paths,
stage budgets and clean-core inheritance before publishing any final freeze.
`previous_candidate_reproduction.json` preserves the v1 failure reproduction.

Local and remote CPU fixture regressions passed with the real result schema,
both 2560/3584 core budgets, reuse of completed O1/O2 fixtures and rejection of
config/checkpoint mismatches. These checks execute no model forward or training
and establish neither derivative correctness nor method efficacy. Evidence is
in `validation_local.json`, `validation_remote.json` and
`publication_receipt.json`; the remote validation SHA256 is
`f08e47a038f6b2a93b6a33d9e24ab731fe7dabe72829a9200850c774954e97a3`.

No production training or controller was modified. Do not activate the queue
while a numerical leaf is active. A recorded handoff at a completed/waiting
scientific-review boundary must preserve old evidence, the original logical job
dependency and explicit process-identity verification. Every actual clean stage
still needs its full artifact audit, CPU Adam replay and scientific review.
Independent seed confirmation has a separate prepared correction in
`../confirmation_clean_pairing_20261110_1430/`.
