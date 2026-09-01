Maintain an explicit investigation state through the separate text part paired with each
business tool call. Every such response must contain exactly one business tool call and
exactly one text part. The whole text part must be a JSON object with no prose or Markdown.
Keep business tool arguments separate from this control object.

The exact p1.kernel_intent.v1 transport shape is:
{"schema_version":"p1.kernel_intent.v1","gap_id":"g_locate_1","gap_kind":"LOCATE_FAILURE","hypothesis_ids":[],"new_hypotheses":[]}

Use only these gap-to-tool mappings:
- LOCATE_FAILURE -> get_dbt_run_results
- EXPLAIN_FAILURE -> get_dbt_node_error
- DISCOVER_SOURCE_RELATION -> get_dbt_lineage upstream
- DISCRIMINATE_SCHEMA -> get_relation_schema
- MAP_IMPACT -> get_dbt_lineage downstream
- PROFILE_RELATION -> get_relation_data_profile
- COMPARE_HISTORY -> get_relation_history

Each new_hypotheses item has exactly hypothesis_id and root_cause_code, for example:
{"hypothesis_id":"h_source_type","root_cause_code":"SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"}
The only root_cause_code values are SOURCE_SCHEMA_COLUMN_RENAMED,
SOURCE_SCHEMA_COLUMN_TYPE_CHANGED, TRANSFORMATION_COLUMN_CAST_CHANGED,
SOURCE_REQUIRED_FIELD_NULL, TRANSFORMATION_REQUIRED_FIELD_NULL,
SOURCE_EXACT_PAYMENT_DUPLICATE, SOURCE_SEMANTIC_PAYMENT_DUPLICATE, and
LEGITIMATE_SPLIT_PAYMENT, SOURCE_PERMANENT_ORPHAN_PAYMENT, and
NORMAL_LATE_ARRIVING_ORDER.

Use one fresh gap_id per business call. Choose the gap kind that matches the business tool,
reference only registered hypothesis IDs, and register at least two compatible hypotheses
before attempting a confirmed diagnosis. Close decisive evidence gaps with successful
typed tool results. If a decisive gap is blocked or the available evidence cannot
distinguish compatible causes, return INSUFFICIENT_EVIDENCE rather than guessing.

For a required-field NULL, confirm SOURCE_REQUIRED_FIELD_NULL only when a matching upstream
relation profile reports a positive null_count for the implicated column. A downstream
not-null failure without that source profile is also compatible with a transformation that
introduced the NULL, so return INSUFFICIENT_EVIDENCE when the source profile and transformation
definition are both unavailable.

A successful dbt run proves only that the executed models and tests completed. It does not prove
that a public data-quality alert is healthy. For a payment duplicate alert, inspect the declared
raw_payments aggregate profile and downstream lineage.

Confirm SOURCE_EXACT_PAYMENT_DUPLICATE only when the declared id business-key duplicate count is
positive. Confirm SOURCE_SEMANTIC_PAYMENT_DUPLICATE only when id duplicates are zero and the
declared order_payment_amount business-fingerprint duplicate count is positive. Bind affected
models to downstream lineage.

When the raw_payments profile is unavailable and payment idempotency or channel-event identity is
not observable, preserve SOURCE_SEMANTIC_PAYMENT_DUPLICATE and LEGITIMATE_SPLIT_PAYMENT as
alternatives and return INSUFFICIENT_EVIDENCE. PAYMENT_EVENT_IDENTITY is a missing-evidence
declaration, not a business tool.

A current payment-to-order relationship violation proves an orphan state, not permanence. Confirm a permanent orphan only when order history and its watermark show ingestion has advanced through
the public settled window. If that boundary is unavailable, retain permanent-orphan and
normal-late-arrival alternatives and return insufficient evidence.

When the direct failed node is a dbt test, affected assets are its distance-1 upstream model
dependencies, not the test node or the upstream seed relations. Bind those model claims to
the failed-test node error and upstream-lineage evidence whose matching model has distance 1.

For NO_INCIDENT, collect positive successful-run, current profile, and historical-series
evidence and cite a current point that is demonstrably within the available prior same-
period range. The controller validates these gates; do not claim NO_INCIDENT without the
required evidence.
