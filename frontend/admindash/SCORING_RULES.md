# Championship scoring rules

Admin → Settings is the shared configuration source. Supported methods are final placement, per confirmed win, and combined. Existing configured per-win values keep their meaning; an unconfigured system defaults to final placement with the existing Major/Minor placement schedules. All point values are nonnegative whole numbers because the existing championship points field is an integer.

Global changes affect new events only. Match and criteria creation save a rule snapshot in the existing result-processing JSON. Criteria events always use final placement awards; judge score calculations remain unchanged. Event-specific overrides require an authorized administrator, an audit reason, renewed approval and republication.

Calculations replace awards for the event rather than incrementing them. BYEs, pending matches, invalid matches and losses do not earn win points. Confirmed forfeits with a valid winner count as wins. Placement awards require complete results and any configured approval. A combined event earns both configured components; there is no implicit champion bonus. Automatic calculation can be disabled; explicit recalculation and authorized publication still calculate the saved rules.

Published department awards are snapshots separate from internal calculations. Public, Faculty championship and Superadmin championship standings aggregate only eligible approved/published outcomes. Corrections withdraw publication and reconcile existing awards. Reopen dependent confirmed matches from the final backwards before correcting an earlier match. Draft score records are retained.

Single-elimination final and third-place games establish those placements; shared elimination cohorts are supported. Round-robin wins and configured head-to-head/score difference/points-scored metrics resolve supported standings. Unresolved ties, unknown metrics or incomplete double-elimination reset outcomes require authorized final-placement resolution in the event override. Shared ties receive the full configured award for their shared position; there is no fractional splitting.

Modern single-round criteria awards use complete approved judge submissions and existing ranking arithmetic. Multiround/ranking-mode overall aggregation remains unavailable in the existing judging engine; round results can still be published, but overall championship points remain pending instead of treating a preliminary result as final.

No additional migration is needed for this redesign. The existing SystemSettings championship JSON and event result JSON hold rules and award snapshots; the existing audit log records changes. Preview/reset/discard do not write event results or standings.
