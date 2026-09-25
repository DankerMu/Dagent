## Intent and functionality

Describe the user-visible change, preserved behavior and acceptance scenario.
Target `dagent` for custom development; `main` is the pure upstream baseline.

## OpenSpec fixture

Link the issue and validated fixture. For documentation-only changes, explain why
subagent-workflow does not apply. Record material decisions in the fixture/PR,
not a second decision-record tree.

## Risk and review

Identify auth, ownership, public contract, data deletion, migration, concurrency,
sandbox or credential changes. Record independent reviewer findings, adjudication
and the subagent-workflow fix-gate outcome. Human review is functional acceptance,
not an assertion that a human inspected implementation code.

## Test evidence

List exact commands, exits, tested scope, test discovery/execution/skips and any
unverified surface. Include red/green evidence for non-trivial new behavior.
Do not weaken a gate, fixture or frozen baseline to obtain green.

## Runtime evidence

Attach actual HTTP/task-state outcomes and browser screenshots/console results for
changed runtime surfaces. Real model evidence is local, uses the configured model,
and contains no credentials. `None — review-only change` requires a reason.

## Compatibility and operations

Explain configuration/schema/docs changes and rollback. Flag CI or baseline
changes explicitly. Automatic merge does not authorize production deployment.
