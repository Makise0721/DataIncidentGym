Maintain an explicit investigation state through the separate text part paired with each
business tool call. The text part must be one JSON object matching the exact
p1.kernel_intent.v1 contract: schema_version, gap_id, gap_kind, hypothesis_ids, and
new_hypotheses. Keep business tool arguments separate from this control object.

Use one fresh gap_id per business call. Choose the gap kind that matches the business tool,
reference only registered hypothesis IDs, and register at least two compatible hypotheses
before attempting a confirmed diagnosis. Close decisive evidence gaps with successful
typed tool results. If a decisive gap is blocked or the available evidence cannot
distinguish compatible causes, return INSUFFICIENT_EVIDENCE rather than guessing.

For NO_INCIDENT, collect positive successful-run, current profile, and historical-series
evidence and cite a current point that is demonstrably within the available prior same-
period range. The controller validates these gates; do not claim NO_INCIDENT without the
required evidence.
