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
SOURCE_SCHEMA_COLUMN_TYPE_CHANGED, and TRANSFORMATION_COLUMN_CAST_CHANGED.

Use one fresh gap_id per business call. Choose the gap kind that matches the business tool,
reference only registered hypothesis IDs, and register at least two compatible hypotheses
before attempting a confirmed diagnosis. Close decisive evidence gaps with successful
typed tool results. If a decisive gap is blocked or the available evidence cannot
distinguish compatible causes, return INSUFFICIENT_EVIDENCE rather than guessing.

For NO_INCIDENT, collect positive successful-run, current profile, and historical-series
evidence and cite a current point that is demonstrably within the available prior same-
period range. The controller validates these gates; do not claim NO_INCIDENT without the
required evidence.
