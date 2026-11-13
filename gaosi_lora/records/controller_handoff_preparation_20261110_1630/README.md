# Idle controller handoff preparation — not activated

The pending two-stage clean corrections require replacement of controllers that
already imported the old queues. Replacing a file on disk cannot change their
loaded code. Stopping an ordinary `tools/job.py` child also makes its wrapper
publish FAIL, which can abort downstream dependencies. This preparation retains
the original logical job path while a separately named, unchanged job.py wrapper
records the replacement queue's actual exit status.

`handoff.py` requires a separately pinned boundary plan, completed leaf result
and job evidence, an absent scientific release, the original queue's explicit
waiting-for-review event, and exact live wrapper/controller PID, ticks, argv,
cwd and child relationships. It stages a replacement behind an activation gate,
stops only the idle old controller and wrapper, archives their job/log bytes,
atomically aliases the original dependency to the replacement job, retires the
stopped old processes, and releases the new queue. The old partial RUNNING state
is preserved as interrupted controller provenance; it is never rewritten as a
scientific PASS or numerical failure. Both old and replacement wall times must
be retained when reporting controller costs.

Before/after alias interruption recovery uses the already recorded replacement
identity and never launches a second queue. The replacement's final status comes
from unmodified job.py. `identity_adapter.py` verifies a separately pinned receipt,
unchanged original launch identities, original evidence, exact alias, new process
identity and blocking environment. It rejects an unpinned receipt, a different
original controller and a wrong alias. It supports live capture and file-only
snapshot verification as explicitly different scopes.

`validation_local_v2.json` covers six real isolated Linux-process cases, including
both interruption points, actual downstream wait_for behavior and a replacement
that fails. Each executed replacement runs exactly once. These fixtures have no
model, forward pass or training. `validation_local.json` preserves the earlier
handoff-only regression before identity-adapter coverage was added.

No production plan or activation exists. Before actual use:

1. Complete the real leaf artifact audit, CPU Adam replay and independent review;
   withhold the continuation receipt during handoff. Recheck the actual idle
   process boundary and prepare the plan from current evidence.
2. Prepare versioned collector and snapshot-review integration using the pinned
   receipt and `identity_adapter.verify`. Preserve the original launch manifest;
   collect both old and replacement evidence and distinguish physical job name
   from logical dependency name. The original observer must have stopped at the
   completed-leaf review boundary. This integration/observer handoff is still
   outstanding; the adapter module alone is not a configured observer.
3. Switch only the idle controller, verify its gate/queue and logical dependency,
   and start the corresponding versioned observer. Then publish the independently
   justified leaf continuation review through the unchanged scientific guard.

Primary candidate: `../organization_clean_pairing_20261110_1620/` v2.
Independent-seed confirmation correction: `../confirmation_clean_pairing_20261110_1430/`;
its controller waits on a different boundary and needs a separately validated
preflight, not an invented completed-meta event.
