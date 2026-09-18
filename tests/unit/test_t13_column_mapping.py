"""T13 slice 2: the narrow column-mapping reader's shape matrix.

All SQL here mirrors the pinned fixture's compiled shapes (staging renames,
arithmetic, multi-CTE chains, two-source equi-joins, aggregates) — everything
outside the documented subset must be an explicit UNKNOWN, and UNKNOWN never
counts as evidence.
"""

from __future__ import annotations

import pytest

from data_incident_gym.column_mapping import (
    UNKNOWN_REASONS,
    UpstreamDefinition,
    map_failing_expression,
    normalize_sql_text,
)

TERMINALS = {"raw_customers", "raw_orders", "raw_payments"}

STG_CUSTOMERS = (
    'with source as (select * from "analytics"."raw_customers"), '
    "renamed as (select id as customer_id, first_name, last_name from source) "
    "select * from renamed"
)
STG_ORDERS = (
    'with source as (select * from "analytics"."raw_orders"), '
    "renamed as (select id as order_id, user_id as customer_id, order_date, status from source) "
    "select * from renamed"
)
STG_PAYMENTS = (
    'with source as (select * from "analytics"."raw_payments"), '
    "renamed as (select id as payment_id, order_id, payment_method, "
    "amount / 100 as amount from source) select * from renamed"
)
CUSTOMERS = (
    'with customers as (select * from "analytics"."stg_customers"), '
    'orders as (select * from "analytics"."stg_orders"), '
    'payments as (select * from "analytics"."stg_payments"), '
    "customer_orders as (select customer_id, min(order_date) as first_order, "
    "max(order_date) as most_recent_order, count(order_id) as number_of_orders "
    "from orders group by customer_id), "
    "customer_payments as (select orders.customer_id, sum(amount) as total_amount "
    "from payments left join orders on payments.order_id = orders.order_id "
    "group by orders.customer_id), "
    "final as (select customers.customer_id, customers.first_name, customers.last_name, "
    "customer_orders.first_order, customer_orders.most_recent_order, "
    "customer_orders.number_of_orders, "
    "customer_payments.total_amount as customer_lifetime_value "
    "from customers "
    "left join customer_orders on customers.customer_id = customer_orders.customer_id "
    "left join customer_payments on customers.customer_id = customer_payments.customer_id) "
    "select * from final"
)
ORDERS = (
    'with orders as (select * from "analytics"."stg_orders"), '
    'payments as (select * from "analytics"."stg_payments"), '
    "order_payments as (select order_id, "
    "sum(case when payment_method = 'credit_card' then amount else 0 end) "
    "as credit_card_amount, sum(amount) as total_amount from payments group by order_id), "
    "final as (select orders.order_id, orders.customer_id, orders.order_date, orders.status, "
    "order_payments.credit_card_amount, order_payments.total_amount as amount "
    "from orders left join order_payments on orders.order_id = order_payments.order_id) "
    "select * from final"
)
UPSTREAM = {
    "analytics.stg_customers": UpstreamDefinition(STG_CUSTOMERS),
    "analytics.stg_orders": UpstreamDefinition(STG_ORDERS),
    "analytics.stg_payments": UpstreamDefinition(STG_PAYMENTS),
}
TWO_SOURCE_JOIN = (
    'with a as (select * from "analytics"."stg_orders"), '
    'b as (select * from "analytics"."stg_payments"), '
    "final as (select a.order_id from a join b on {condition}) select * from final"
)

# The archived compiled text of a real run, copied verbatim: multi-line SQL,
# modifier joins and schema-qualified quoted relation names.
REAL_STG_CUSTOMERS = """\
with source as (
    select * from "data_incident_gym"."analytics"."raw_customers"

),

renamed as (

    select
        id as customer_id,
        first_name,
        last_name

    from source

)

select * from renamed"""
REAL_STG_ORDERS = """\
with source as (
    select * from "data_incident_gym"."analytics"."raw_orders"

),

renamed as (

    select
        id as order_id,
        user_id as customer_id,
        order_date,
        status

    from source

)

select * from renamed"""
REAL_CUSTOMERS = """\
with customers as (

    select * from "data_incident_gym"."analytics"."stg_customers"

),

orders as (

    select * from "data_incident_gym"."analytics"."stg_orders"

),

payments as (

    select * from "data_incident_gym"."analytics"."stg_payments"

),

customer_orders as (

        select
        customer_id,

        min(order_date) as first_order,
        max(order_date) as most_recent_order,
        count(order_id) as number_of_orders
    from orders

    group by customer_id

),

customer_payments as (

    select
        orders.customer_id,
        sum(amount) as total_amount

    from payments

    left join orders on
         payments.order_id = orders.order_id

    group by orders.customer_id

),

final as (

    select
        customers.customer_id,
        customers.first_name,
        customers.last_name,
        customer_orders.first_order,
        customer_orders.most_recent_order,
        customer_orders.number_of_orders,
        customer_payments.total_amount as customer_lifetime_value

    from customers

    left join customer_orders
        on customers.customer_id = customer_orders.customer_id

    left join customer_payments
        on  customers.customer_id = customer_payments.customer_id

)

select * from final"""


def _origins(result) -> list[tuple[str, str]]:
    return [(reference.relation, reference.column) for reference in result.references]


# -- the two acceptance shapes ----------------------------------------------


def test_the_customers_join_resolves_both_renamed_origins() -> None:
    """T1′: the failing join's sides trace to raw_customers.id (renamed by
    stg_customers) and raw_orders.user_id (renamed by stg_orders)."""

    result = map_failing_expression(
        CUSTOMERS,
        "operator does not exist: text = integer\n"
        "  LINE 42:     on customers.customer_id = customer_orders.customer_id",
        upstream=UPSTREAM,
        terminal_relations=TERMINALS,
    )

    assert result.status == "RESOLVED"
    assert result.expression == "customers.customer_id = customer_orders.customer_id"
    # The order follows the condition's own sides: T2′ (the mirror pair) needs
    # the left origin and the right origin to stay distinguishable.
    assert _origins(result) == [("raw_customers", "id"), ("raw_orders", "user_id")]
    # The unrelated third join and the other CTEs never entered the subgraph.
    assert all("customer_payments" not in reference.chain for reference in result.references)


def test_the_orders_join_resolves_both_origins() -> None:
    """Extra shape coverage (not an acceptance pair): order_id is raw_orders.id
    renamed by stg_orders; the right side is raw_payments.order_id passed
    through unchanged."""

    result = map_failing_expression(
        ORDERS,
        "operator does not exist: text = integer\n"
        "  LINE 30:     on orders.order_id = order_payments.order_id",
        upstream=UPSTREAM,
        terminal_relations=TERMINALS,
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_orders", "id"), ("raw_payments", "order_id")]


def test_the_real_compiled_text_shape_resolves_the_customers_join() -> None:
    """The archived compiled text — multi-line, ``left join``, schema-qualified
    quoted names, definitions keyed by their qualified relation name — is the
    input the reader will actually see; the accepted shapes must hold on it."""

    result = map_failing_expression(
        REAL_CUSTOMERS,
        "operator does not exist: text = integer\n"
        "  LINE 44:         on customers.customer_id = customer_orders.customer_id",
        upstream={
            "data_incident_gym.analytics.stg_customers": UpstreamDefinition(REAL_STG_CUSTOMERS),
            "data_incident_gym.analytics.stg_orders": UpstreamDefinition(REAL_STG_ORDERS),
        },
        terminal_relations=TERMINALS,
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_customers", "id"), ("raw_orders", "user_id")]


def test_a_projection_with_arithmetic_maps_to_its_source_column() -> None:
    result = map_failing_expression(
        STG_PAYMENTS,
        "operator does not exist: text / integer\n  LINE 20:         amount / 100 as amount",
        upstream={},
        terminal_relations=TERMINALS,
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_payments", "amount")]


def test_an_aggregate_maps_to_the_columns_it_reads() -> None:
    result = map_failing_expression(
        ORDERS,
        "aggregate failure\n  LINE 15: sum(case when payment_method = 'credit_card' "
        "then amount else 0 end) as credit_card_amount",
        upstream=UPSTREAM,
        terminal_relations=TERMINALS,
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_payments", "payment_method"), ("raw_payments", "amount")]


# -- identification rules ----------------------------------------------------


def test_an_unrelated_message_is_an_explicit_unknown() -> None:
    result = map_failing_expression(
        ORDERS, "some unrelated failure text", upstream=UPSTREAM, terminal_relations=TERMINALS
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "EXPRESSION_NOT_IDENTIFIED"


def test_a_hit_inside_a_longer_reference_is_not_an_identification() -> None:
    """``customer_id`` inside ``customers.customer_id`` is another expression:
    mapping the substring would report a column the message never named."""

    sql = (
        'with orders as (select * from "analytics"."stg_orders"), '
        "final as (select customer_id from orders) select * from final"
    )

    result = map_failing_expression(
        sql,
        "LINE 4:     on customers.customer_id = customer_orders.customer_id",
        upstream=UPSTREAM,
        terminal_relations=TERMINALS,
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "EXPRESSION_NOT_IDENTIFIED"


def test_a_hit_bounded_by_punctuation_still_matches() -> None:
    sql = (
        'with source as (select * from "analytics"."raw_customers"), '
        "renamed as (select id as customer_id from source) select * from renamed"
    )

    result = map_failing_expression(
        sql, "LINE 3:         id as customer_id,", upstream={}, terminal_relations=TERMINALS
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_customers", "id")]


def test_two_maximal_hits_stay_ambiguous() -> None:
    """Both join conditions appear in the message: nothing may be guessed."""

    result = map_failing_expression(
        CUSTOMERS,
        "LINE 42: customers.customer_id = customer_orders.customer_id "
        "LINE 44: customers.customer_id = customer_payments.customer_id",
        upstream=UPSTREAM,
        terminal_relations=TERMINALS,
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "EXPRESSION_AMBIGUOUS"


# -- relation scope ----------------------------------------------------------


def test_a_relation_alias_replaces_the_relation_name() -> None:
    """The scope maps SQL-visible aliases to relations; source order in the
    joined condition must not be inverted by the alias names."""

    sql = (
        "select * from raw_orders as raw_customers "
        "join raw_customers as raw_orders on raw_customers.id = raw_orders.id"
    )

    result = map_failing_expression(
        sql,
        "LINE 2:     on raw_customers.id = raw_orders.id",
        terminal_relations=TERMINALS,
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_orders", "id"), ("raw_customers", "id")]


def test_an_aliased_relation_is_not_visible_under_its_own_name() -> None:
    result = map_failing_expression(
        "select * from raw_orders as o join raw_customers as c on raw_orders.id = c.id",
        "LINE 2:     on raw_orders.id = c.id",
        terminal_relations=TERMINALS,
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "UNKNOWN_SOURCE"


# -- relations that can end a walk -------------------------------------------


def test_a_declared_terminal_relation_ends_the_trace() -> None:
    """Raw inputs are not models: a relation the caller declares as a
    seed/source is its own origin."""

    result = map_failing_expression(
        STG_CUSTOMERS,
        "LINE 3:     select id as customer_id",
        upstream={},
        terminal_relations={"raw_customers"},
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_customers", "id")]


def test_a_relation_without_definition_or_terminal_status_is_unknown() -> None:
    """A model whose definition was withheld is not an origin, even when the
    walk reaches it as a plain relation name."""

    result = map_failing_expression(
        'select customer_id / 2 as customer_id from "analytics"."stg_customers"',
        "LINE 2: customer_id / 2 as customer_id",
        upstream={},
        terminal_relations=TERMINALS,
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "DEFINITION_MISSING"


def test_an_undeclared_relation_name_is_not_a_terminal() -> None:
    sql = (
        'with orders as (select * from "analytics"."stg_orders"), '
        "final as (select orders.order_id from orders "
        "left join other_facts on orders.order_id = other_facts.order_id) select * from final"
    )

    result = map_failing_expression(
        sql,
        "LINE 4: on orders.order_id = other_facts.order_id",
        upstream=UPSTREAM,
        terminal_relations=TERMINALS,
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "DEFINITION_MISSING"
    # No half-answer: the resolvable side is not reported on its own.
    assert result.references == ()


# -- unsupported shapes ------------------------------------------------------


def test_a_window_function_is_unsupported() -> None:
    sql = (
        'with source as (select * from "analytics"."raw_orders"), '
        "renamed as (select row_number() over (partition by user_id) as rn from source) "
        "select * from renamed"
    )

    result = map_failing_expression(
        sql, "LINE 5: row_number() over (partition by user_id) as rn", upstream={}
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "UNSUPPORTED_EXPRESSION"


def test_a_subquery_is_unsupported() -> None:
    sql = (
        'with source as (select * from "analytics"."raw_orders"), '
        "renamed as (select (select max(id) from source) as biggest from source) "
        "select * from renamed"
    )

    result = map_failing_expression(
        sql, "LINE 5: (select max(id) from source) as biggest", upstream={}
    )

    assert result.status == "UNKNOWN"
    assert result.reason in UNKNOWN_REASONS


def test_a_non_equi_join_condition_is_refused() -> None:
    result = map_failing_expression(
        TWO_SOURCE_JOIN.format(condition="a.order_id > b.order_id"),
        "LINE 3:     on a.order_id > b.order_id",
        upstream=UPSTREAM,
        terminal_relations=TERMINALS,
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "UNSUPPORTED_EXPRESSION"


def test_a_conjunctive_join_condition_is_refused() -> None:
    """Two equi-conditions are not the documented single-pair shape; splitting
    them would report a pair the failing expression never named."""

    result = map_failing_expression(
        TWO_SOURCE_JOIN.format(
            condition="a.order_id = b.order_id and a.customer_id = b.customer_id"
        ),
        "LINE 3:     on a.order_id = b.order_id and a.customer_id = b.customer_id",
        upstream=UPSTREAM,
        terminal_relations=TERMINALS,
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "UNSUPPORTED_EXPRESSION"


def test_a_definition_that_is_not_complete_is_refused() -> None:
    result = map_failing_expression(
        ORDERS,
        "LINE 30:     on orders.order_id = order_payments.order_id",
        upstream={
            **UPSTREAM,
            "analytics.stg_orders": UpstreamDefinition(STG_ORDERS, complete=False),
        },
        terminal_relations=TERMINALS,
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "DEFINITION_INCOMPLETE"


@pytest.mark.parametrize(
    "definition",
    [
        pytest.param(
            "select id as x from raw_orders union all select user_id as x from raw_payments",
            id="set-operator",
        ),
        pytest.param("select id as x from raw_orders, raw_customers", id="comma-join"),
        pytest.param("select id as x from raw_orders join raw_customers", id="join-without-on"),
    ],
)
def test_a_definition_that_cannot_be_fully_consumed_is_refused(definition: str) -> None:
    """A recognizable prefix is not a parse: the union branch, the comma-joined
    source and the unconditional join all refuse the definition."""

    result = map_failing_expression(
        'with model as (select x / 2 as x from "analytics"."odd_model") select * from model',
        "LINE 2:     x / 2 as x",
        upstream={"analytics.odd_model": UpstreamDefinition(definition)},
        terminal_relations=TERMINALS,
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "UNSUPPORTED_SELECT"


def test_a_cyclic_definition_chain_is_refused() -> None:
    first = (
        'with source as (select * from "analytics"."second"), '
        "renamed as (select value from source) select * from renamed"
    )
    second = (
        'with source as (select * from "analytics"."first"), '
        "renamed as (select value from source) select * from renamed"
    )

    result = map_failing_expression(
        first,
        "LINE 2: renamed as (select value from source)",
        upstream={
            "analytics.first": UpstreamDefinition(first),
            "analytics.second": UpstreamDefinition(second),
        },
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "DEPTH_EXCEEDED"


def test_an_ambiguous_unqualified_column_is_refused() -> None:
    sql = (
        'with orders as (select * from "analytics"."stg_orders"), '
        'payments as (select * from "analytics"."stg_payments"), '
        "final as (select order_id from orders "
        "left join payments on orders.order_id = payments.order_id) select * from final"
    )

    result = map_failing_expression(
        sql,
        "LINE 5:         select order_id,",
        upstream=UPSTREAM,
        terminal_relations=TERMINALS,
    )

    assert result.status == "UNKNOWN"
    assert result.reason in {"AMBIGUOUS_COLUMN", "UNKNOWN_COLUMN"}


def test_duplicate_definition_keys_are_a_caller_error() -> None:
    with pytest.raises(ValueError):
        map_failing_expression(
            STG_PAYMENTS,
            "LINE 20:         amount / 100 as amount",
            upstream={
                "analytics.raw_payments": UpstreamDefinition(STG_PAYMENTS),
                "other.raw_payments": UpstreamDefinition(STG_PAYMENTS, complete=False),
            },
            terminal_relations=TERMINALS,
        )


def test_identification_ignores_whitespace_and_comment_differences() -> None:
    sql = (
        'with source as (select * from "analytics"."raw_payments"), '
        "renamed as (\n    select\n        id as payment_id,\n"
        "        -- `amount` is stored in cents\n        amount / 100 as amount\n"
        "    from source\n) select * from renamed"
    )

    result = map_failing_expression(
        sql, "LINE 20:         amount / 100 as amount", upstream={}, terminal_relations=TERMINALS
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_payments", "amount")]
    assert normalize_sql_text("A  B\n C") == "a b c"
