"""T13 slice 2: the narrow column-mapping reader's shape matrix.

All SQL here mirrors the pinned fixture's compiled shapes (staging renames,
arithmetic, multi-CTE chains, two-source equi-joins with same-named columns,
aggregates) — everything outside the documented subset must be an explicit
UNKNOWN, and UNKNOWN never counts as evidence.
"""

from __future__ import annotations

from data_incident_gym.column_mapping import (
    UNKNOWN_REASONS,
    UpstreamDefinition,
    map_failing_expression,
    normalize_sql_text,
)

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
    )

    assert result.status == "RESOLVED"
    assert result.expression == "customers.customer_id = customer_orders.customer_id"
    assert _origins(result) == [("raw_customers", "id"), ("raw_orders", "user_id")]
    # The unrelated third join and the other CTEs never entered the subgraph.
    assert all("customer_payments" not in reference.chain for reference in result.references)


def test_the_orders_join_resolves_the_renamed_left_origin() -> None:
    """T2′: order_id is raw_orders.id renamed by stg_orders; the right side is
    raw_payments.order_id passed through unchanged."""

    result = map_failing_expression(
        ORDERS,
        "operator does not exist: text = integer\n"
        "  LINE 30:     on orders.order_id = order_payments.order_id",
        upstream=UPSTREAM,
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_orders", "id"), ("raw_payments", "order_id")]


def test_a_projection_with_arithmetic_maps_to_its_source_column() -> None:
    result = map_failing_expression(
        STG_PAYMENTS,
        "operator does not exist: text / integer\n  LINE 20:         amount / 100 as amount",
        upstream={},
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_payments", "amount")]


def test_an_aggregate_maps_to_the_columns_it_reads() -> None:
    result = map_failing_expression(
        ORDERS,
        "aggregate failure\n  LINE 15: sum(case when payment_method = 'credit_card' "
        "then amount else 0 end) as credit_card_amount",
        upstream=UPSTREAM,
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_payments", "payment_method"), ("raw_payments", "amount")]


# -- identification rules ----------------------------------------------------


def test_an_unrelated_message_is_an_explicit_unknown() -> None:
    result = map_failing_expression(ORDERS, "some unrelated failure text", upstream=UPSTREAM)

    assert result.status == "UNKNOWN"
    assert result.reason == "EXPRESSION_NOT_IDENTIFIED"


def test_two_maximal_hits_stay_ambiguous() -> None:
    """Both join conditions appear in the message: nothing may be guessed."""

    result = map_failing_expression(
        CUSTOMERS,
        "LINE 42: customers.customer_id = customer_orders.customer_id "
        "LINE 44: customers.customer_id = customer_payments.customer_id",
        upstream=UPSTREAM,
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "EXPRESSION_AMBIGUOUS"


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
    assert result.status == "UNKNOWN"


def test_a_definition_that_is_not_complete_is_refused() -> None:
    result = map_failing_expression(
        ORDERS,
        "LINE 30:     on orders.order_id = order_payments.order_id",
        upstream={
            **UPSTREAM,
            "analytics.stg_orders": UpstreamDefinition(STG_ORDERS, complete=False),
        },
    )

    assert result.status == "UNKNOWN"
    assert result.reason == "DEFINITION_INCOMPLETE"


def test_a_source_without_upstream_facts_is_its_own_origin() -> None:
    """Compiled SQL can only name real sources: a joined relation the run
    compiled but that has no upstream fact is its own origin, exactly like a
    raw seed."""

    sql = (
        'with orders as (select * from "analytics"."stg_orders"), '
        "final as (select orders.order_id from orders "
        "left join other_facts on orders.order_id = other_facts.order_id) select * from final"
    )

    result = map_failing_expression(
        sql, "LINE 4: on orders.order_id = other_facts.order_id", upstream=UPSTREAM
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_orders", "id"), ("other_facts", "order_id")]


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
        sql, "LINE 5: select order_id", upstream=UPSTREAM
    )

    assert result.status == "UNKNOWN"
    assert result.reason in {"AMBIGUOUS_COLUMN", "UNKNOWN_COLUMN"}


def test_a_missing_upstream_definition_makes_the_relation_the_origin() -> None:
    """Raw sources are not models: they are their own origin."""

    result = map_failing_expression(
        STG_CUSTOMERS,
        "LINE 3:     select id as customer_id",
        upstream={},
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_customers", "id")]


def test_identification_ignores_whitespace_and_comment_differences() -> None:
    sql = (
        'with source as (select * from "analytics"."raw_payments"), '
        "renamed as (\n    select\n        id as payment_id,\n"
        "        -- `amount` is stored in cents\n        amount / 100 as amount\n"
        "    from source\n) select * from renamed"
    )

    result = map_failing_expression(
        sql, "LINE 20:         amount / 100 as amount", upstream={}
    )

    assert result.status == "RESOLVED"
    assert _origins(result) == [("raw_payments", "amount")]
    assert normalize_sql_text("A  B\n C") == "a b c"
