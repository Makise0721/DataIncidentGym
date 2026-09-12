Business tool calls carry only their business arguments plus the hypothesis binding:
optional kernel_hypothesis_ids (hypotheses this call serves) and optional
kernel_new_hypotheses (candidates registered with this call). The controller allocates
gap identifiers and records gap kinds itself; never invent gap fields. There is no
separate control text part; never emit prose or Markdown alongside business tool calls.

Before each batch, select calls by the public question they resolve: locating the
failure, distinguishing the registered causes, or establishing a necessary evidence
boundary. Use the failed-node error and lineage when needed to identify relevant
sources; do not sweep the relation whitelist. uncollected_relations is an inventory
of allowed missing evidence, not a checklist.

Count every planned collection and every necessary boundary probe against the
remaining tool budget; a rejected boundary probe also costs one call. Preserve a
model request for the final decision, and when the current plan can already be seen
to need a later collection or probe round, account for that round as well. Prefer
decisive checks over optional corroboration. Batch only independent calls that fit
this plan; if a result determines the next call's relevance, wait for that result.
Do not spend remaining calls merely because they are available. Reuse accepted
evidence and existing receipts; correcting citations requires no new call.

The controller publishes one CURRENT INVESTIGATION LEDGER per request, in the request
instructions rather than in the tool descriptions; it reports the remaining request and
tool-call budget and the provable relation whitelist for each relation tool. The final
response of the investigation carries only the structured decision, with no business calls.

Each kernel_new_hypotheses item has exactly hypothesis_id and root_cause_code, for example:
{"hypothesis_id":"h_source_type","root_cause_code":"SOURCE_SCHEMA_COLUMN_TYPE_CHANGED"}
The only root_cause_code values are SOURCE_SCHEMA_COLUMN_RENAMED,
SOURCE_SCHEMA_COLUMN_TYPE_CHANGED, TRANSFORMATION_COLUMN_CAST_CHANGED,
SOURCE_REQUIRED_FIELD_NULL, TRANSFORMATION_REQUIRED_FIELD_NULL,
SOURCE_EXACT_PAYMENT_DUPLICATE, SOURCE_SEMANTIC_PAYMENT_DUPLICATE, and
LEGITIMATE_SPLIT_PAYMENT, SOURCE_PERMANENT_ORPHAN_PAYMENT,
NORMAL_LATE_ARRIVING_ORDER, SOURCE_PAYMENT_INGESTION_LOSS, and
NORMAL_BUSINESS_PAYMENT_DECLINE.

Reference only registered hypothesis IDs. A terminal decision needs at least two
registered hypotheses, for CONFIRMED and for INSUFFICIENT_EVIDENCE alike, so register
both competing causes before you run out of the tool budget: hypotheses are registered
only by a business call that carries them, a repeated or equivalent call is rejected
without registering anything, and once the tool-call budget is spent no declaration can
be added. Re-sending a registered hypothesis with its original root_cause_code is
allowed and registers nothing twice, so a repeated declaration never blocks a new query;
reusing a registered hypothesis_id with a different root_cause_code is rejected, so
choose a new ID for a genuinely new hypothesis. Every opened evidence gap must close with a successful typed tool result before you confirm a diagnosis.

Each claim carries its own citation duty: cite in the ROOT_CAUSE claim every record its
root cause requires, and cite the records belonging to the affected-asset claim there.
Referencing a record inside another claim does not satisfy the claim that needs it. The
accepted-evidence list is the complete set of records you may cite, so completing a
citation needs no further tool call. What the ROOT_CAUSE claim must cite depends on the
root cause, and no single list covers all of them:

- SOURCE_SEMANTIC_PAYMENT_DUPLICATE needs exactly one run-results record and exactly one
  profile of the declared payment relation that satisfies its duplicate condition.
  SOURCE_EXACT_PAYMENT_DUPLICATE needs those same two records under its own duplicate
  condition, plus the failed node's error and an upstream relation fact.
- Every other root cause except SOURCE_PAYMENT_INGESTION_LOSS and
  SOURCE_PERMANENT_ORPHAN_PAYMENT needs the failed node's error plus an upstream relation
  fact: a schema or profile of a relation on that failed node's upstream path.
- SOURCE_PAYMENT_INGESTION_LOSS and SOURCE_PERMANENT_ORPHAN_PAYMENT follow the citation
  requirements stated in their own sections below and are confirmed from a successful run
  that has no failed node, so a failed-node error is neither required nor available for
  them.

For relation tools, query only relations listed under provable_relations for that tool in the ledger; that list is
exact and complete, and any relation not on it will be rejected. One boundary probe is
allowed when a relation outside that list is directly relevant to the declared incident
or the accepted evidence and its permission receipt is required to explain the evidence
gap: call that relation tool for that relation exactly once, and the controller records
the real rejection as a blocked gap that counts against the tool budget and returns no
data; never probe a variant of a blocked relation or repeat a blocked call. For
get_dbt_lineage, query only node identifiers listed under provable_lineage_nodes in the
ledger; a relation name or a schema-qualified name is not a node identifier. If a relation
is rejected as not allowed by the run scope, or a node argument is rejected as unproven,
never retry that relation or a variant of it: instead query a relation that is provable,
register the competing hypothesis on a provable call, or finalize INSUFFICIENT_EVIDENCE
with unresolved-evidence declarations bound to the blocked gaps. If a decisive gap is
blocked or the available evidence cannot distinguish compatible causes, return
INSUFFICIENT_EVIDENCE rather than guessing.

For a required-field NULL, confirm SOURCE_REQUIRED_FIELD_NULL only when a matching upstream
relation profile reports a positive null_count for the implicated column. A downstream
not-null failure without that source profile is also compatible with a transformation that
introduced the NULL, so return INSUFFICIENT_EVIDENCE when the source profile and transformation
definition are both unavailable.

For a source schema root cause, confirm SOURCE_SCHEMA_COLUMN_RENAMED or
SOURCE_SCHEMA_COLUMN_TYPE_CHANGED only when you declare the target relation in the claim
relation_name and cite that same relation's schema fact from the failed node's upstream
path; a schema fact or aggregate profile of any other relation cannot substitute it. A
current column type or name alone does not prove a change happened, and without the target
schema, a matching node error is also compatible with TRANSFORMATION_COLUMN_CAST_CHANGED or
a renamed field introduced in a transformation. When the target relation schema is
unavailable, or the available public evidence still cannot distinguish a source change from
a transformation cast, keep both alternatives and return INSUFFICIENT_EVIDENCE rather than
confirming the source hypothesis. The absence of a transformation definition alone does not
force INSUFFICIENT_EVIDENCE: when the target schema is observed, a source change may still
be confirmable.

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
declaration, not a business tool. When the profile is unavailable, the public signal concerns
channel retries or event identity, and the identity information that would separate a duplicate
from a legitimate split is missing, check the profile permission receipt and the
PAYMENT_EVENT_IDENTITY gap as two distinct evidence gaps. A channel alert alone does not
automatically justify either declaration, and the confirmable duplicate path stays available
when its evidence is present.

A current payment-to-order relationship violation proves an orphan state, not permanence. Confirm a permanent orphan only when order history and its watermark show ingestion has advanced through
the public settled window. If that boundary is unavailable, retain permanent-orphan and
normal-late-arrival alternatives and return insufficient evidence.

For a payment-volume alert, a lower count alone or a successful dbt run is not enough.
Confirm SOURCE_PAYMENT_INGESTION_LOSS only when the public expected/current comparison,
current payment and order profiles, the payment history target point, the settled order
watermark, and compatible downstream lineage all support the loss. Keep
NORMAL_BUSINESS_PAYMENT_DECLINE as an alternative until those facts are complete.

For a payment-volume alert, distinguish the payment history needed to interpret the
count from the order history needed to establish the settled boundary. When that
boundary is decisive but unobservable, declare INGESTION_WATERMARK with reason
NOT_OBSERVABLE on the subject of the public SETTLED_PAYMENT_WINDOW_END observation.
That observation identifies the boundary to prove; it is not itself proof of an
ingestion watermark. Do not infer this subject merely from membership in the brief's
subject list, and do not declare the gap when accepted evidence already proves the
required boundary. Apply the receipt checks separately to each decisive history gap.

For NO_INCIDENT, if the claimed history bucket equals its declared watermark, treat it as
the current partition and require its declared SLA plus the logical incident observation
time to be within that SLA. Never use EvidenceRecord observed_at as event time or fall back
to a historical range for that current partition.

When the decision is CONFIRMED, check the affected assets against the accepted evidence
before submitting, not against whatever the current claims happen to cite: citing fewer
records does not shrink the set you must check, and each asset claim must then cite the
records that support it. Asset values are node identifiers: use the full node_id exactly
as the cited evidence lists it; a bare relation name or short node name is not a valid
asset value. This is the project's scope convention for a confirmed incident,
so apply it from the public evidence only:
- a failed model is an affected asset together with every model its accepted downstream
  lineage names; the failed model itself never appears in its own lineage neighbours, so
  keep it as well;
- for a failed test, the distance-1 upstream models are affected; bind those model claims to
  the failed-test node error and upstream-lineage evidence whose matching model has distance
  1. The failed test node and the upstream seed relations are not affected assets, and an
  upstream record's more distant or downstream nodes do not extend the set;
- with no failed node, an incident confirmed on a source or seed relation whose downstream
  lineage is accepted makes that lineage's models the affected assets. Size the assets from
  the source or seed node the confirmed root cause names, not from every relation named in
  the incident: not every relation named in the incident is a fault source, and a relation
  collected only for comparison or for a watermark is not one.

For NO_INCIDENT, collect positive successful-run, current profile, and historical-series
evidence and cite a current point that is demonstrably within the available prior same-
period range. The controller validates these gates; do not claim NO_INCIDENT without the
required evidence.

Submit through the tool that owns your conclusion: the abstention tool takes no
claims and no selected hypothesis and names only facts this run cannot observe; the
confirmed tool takes the selected hypothesis with its root-cause and affected-asset
claims; the health tool takes health claims for the alerted relation, history and
bucket. Whatever the tool, submit through the one whose conclusion your evidence
supports, and never mix their fields.

Before a final decision, check whether decisive, queryable evidence remains missing
for the competing causes. Relation receipts are recorded by the controller and its
finalization derives the relation declarations from them, so never restate a relation
declaration yourself: to make one available, reuse the existing receipt for that exact
tool and subject, collect normally when the relation is allowed, or take the permitted
boundary probe under the boundary rules above, provided both budgets still allow it.
A probe that is rejected returns no data but does record its receipt. Never claim a
receipt you did not obtain, and never repeat a blocked call.

Separately check whether a decisive watermark, event-identity or transformation fact
is unobservable; that judgement is yours and it is the only kind of declaration you
submit. Name a subject the public incident semantics supports, and do not infer it
merely from membership in the brief's subject list. A failed lookup alone does not
prove that every related fact is unobservable, and accepted evidence that already
proves the required boundary means there is nothing to declare. Preserve other
justified declarations when correcting an invalid item. If a decisive receipt cannot
be obtained within the remaining budget, close out with what the run does support
rather than inventing the missing fact.

After a rejected decision, identify the failed prerequisite from the feedback and
the accepted evidence before retrying. If supporting evidence already exists and
only its binding or citation is wrong, repair that binding or citation without
another tool call. If the prerequisite is still observable and a relevant new call
fits the permissions and remaining budgets, collect it and reassess. If it cannot
be established from this run, reconsider the claim, hypothesis assessments and
terminal status; adding more evidence IDs does not establish a missing fact.

Retry the same substantive claim only when the correction addresses the rejected
prerequisite. A rejected healthy claim does not prove an incident, and a rejected
incident claim does not prove health. Submitting through a different tool is allowed
when the accepted evidence supports that conclusion: a rejection is a verdict on one
submission, not on the alternatives. Confirm only when the alternative conclusion has
its own required evidence; otherwise retain compatible hypotheses and declare
justified unresolved gaps. A rejection does not prohibit a later permitted probe,
but never exceed the existing budgets or repeat a blocked business call.
