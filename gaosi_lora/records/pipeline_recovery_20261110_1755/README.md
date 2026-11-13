# Interrupted observer download — recovery pending SSH

The original observer `../pipeline_observer_20261110_1315/` exited after
read-only transport retries exhausted while downloading the already completed
17:45 UTC capture. Its original 37-row ledger, logs and error state are retained.
The collector was submitted exactly once and returned zero. No training or
original controller has been stopped, restarted or changed.

The existing SSH maintainer is PID896141/start_ticks146063673. Verify its actual
`/proc` identity and its current state under
`../scheduled_review_recovery_20261109_0855/` before waiting. Repeated gateway
resets before key exchange are a connection failure; they do not prove that
training stopped. Do not launch a second transport maintainer.

The 5,505,024-byte download prefix matches all 21 complete authenticated manifest
chunks. `partial_capture_result.json` is the complete result.json tar member
read from that verified prefix. At 2026-11-10 17:45:45 UTC it reports 27 complete
O2 episodes and 216/512 committed steps. `partial_capture_review.json` records
the provenance and limits: the full archive and snapshot manifest have not been
independently rehashed, checkpoint tensors and event budgets have not been
reaudited, and present remote liveness remains unknown. This is historical
collector evidence, not a full snapshot review or efficacy conclusion.

Current recovery entry points, not yet successfully run:

1. Once the existing SSH master is live, run `resume_archive_v3.py`. It checks
   the original observer is terminal, reads current remote process identities,
   resumes the existing archive through the unchanged chunk transport helper,
   and checks the full result member equals the previously recovered partial
   result. It never resubmits the collector. At preparation, `preflight.json`,
   `archive_transfer/` and `capture_recovery.json` do not exist. If a later
   attempt partially succeeds, inspect its receipts rather than rerunning
   blindly; preserve the old transfer directory and resume the existing prefix
   through the same helper using a fresh transfer directory if needed.
2. Run `launch_observer_v2.py` exactly once only after the capture has been
   recovered. It preserves the 37 ledger rows byte for byte, appends the one
   recovered capture, publishes pinned sources, and launches only the fresh
   read-only observer `../pipeline_observer_20261110_1755/`. Its original due
   boundary is retained: if overdue, the observer collects it once immediately,
   then schedules from the actual capture time plus 1800 seconds. It does not
   replay all missed intervals. Inspect `launch.json`, `start_receipt.json` and
   actual `/proc` identity if the tool reply is lost; never launch twice.
3. If the new observer immediately collects the overdue capture, wait on its
   real identity and ledger until it returns to a waiting or scientific review
   boundary. Review the recovered 17:45 capture against the verified 17:15
   capture with the new observer's unchanged `review_observation.py`, then
   `review_meta_progress.py`. Review the subsequent actual capture in order.
   A completed leaf still requires the full artifact audit, CPU Adam replay
   and separate scientific review; none of this preparation authorizes it.
4. Publish the two historical local notes (newest first:
   `partial_capture_local_note.json`, then `outage_local_note.json`) to remote REPORT.md and
   NEXT_STEPS.md, preserving existing remote document bytes. Record and sync an
   explicit publication receipt. Then use the new observer's
   `record_progress_note.py` for normal current progress. The two historical
   note receipts truthfully state that their original publication was local.

`resume_archive_attempt1.py` preserves a local argument-encoding failure before
any remote invocation. The pinned `resume_archive.py` preserves the subsequent
read-only preflight attempt whose transport retries exhausted.
`launch_observer.py` is the earlier uninvoked preparation requiring a future due
boundary. These exact original bytes remain at the paths pinned in the outage
note. The later v3/v2 files retain the additional partial-result equality check
and overdue scheduling support without changing those references. No production
observer has been launched from the fresh directory yet.

After recovery, future controller handoff work must carry forward the latest
observer ledger, not the older 13:15 ledger. The prepared clean-control
corrections and handoff monitor registry remain inactive. Full protocol scope
is unchanged; method_validated=false and full_goal_complete=false.
