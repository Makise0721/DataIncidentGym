"""T13 slice 2: the narrow column-mapping reader (design §2.2).

The reader answers one question: **which source relation and column does the
failing expression actually read?** It is deliberately narrow:

- the failing expression is *identified* from the node error message by finding
  exactly one candidate expression (a projection or a join condition of the
  node's SQL) whose normalized text occurs in the message; zero or several
  matches is an explicit UNKNOWN. Matching text is only used to select the
  expression, never to map a column by similarity;
- only the **dependency subgraph of that expression** is parsed: the CTE/alias
  chains the expression's two sides reference. Unrelated joins, aggregations or
  clauses elsewhere in the same SQL are ignored and never trigger UNKNOWN;
- inside that subgraph only the documented shapes are supported (single-source
  alias projections, arithmetic/cast expressions, multi-CTE chains, two-source
  equi-joins with qualified same-named columns, aggregates over grouped keys).
  Anything else — window functions, subqueries, three-source joins, functions
  the keyword table does not know — is an explicit UNKNOWN.

UNKNOWN never counts as evidence in any direction. Definitions that are not
``complete`` (truncated or redaction-changed, per the E2 fact) must not be
passed in as usable: the caller marks them and the reader refuses to resolve
through them.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal

#: Fixed UNKNOWN reasons; callers must not invent new ones.
UNKNOWN_REASONS = frozenset(
    {
        "EXPRESSION_NOT_IDENTIFIED",
        "EXPRESSION_AMBIGUOUS",
        "UNSUPPORTED_EXPRESSION",
        "UNSUPPORTED_SELECT",
        "UNKNOWN_SOURCE",
        "UNKNOWN_COLUMN",
        "AMBIGUOUS_COLUMN",
        "DEFINITION_INCOMPLETE",
        "DEPTH_EXCEEDED",
    }
)

#: Keywords and function names the token scanner may see inside an expression.
#: Anything outside this table (plus identifiers, literals and operators) makes
#: the expression unsupported.
_EXPRESSION_KEYWORDS = frozenset(
    {
        "and",
        "or",
        "not",
        "case",
        "when",
        "then",
        "else",
        "end",
        "is",
        "null",
        "true",
        "false",
        "as",
        "cast",
        "coalesce",
        "nullif",
        "sum",
        "min",
        "max",
        "count",
        "avg",
    }
)
#: Constructs that are never supported inside an expression.
_UNSUPPORTED_PATTERNS = (
    re.compile(r"\bover\s*\(", re.IGNORECASE),
    re.compile(r"\(\s*select\b", re.IGNORECASE),
    re.compile(r"\bfilter\s*\(", re.IGNORECASE),
    re.compile(r"\bdistinct\b", re.IGNORECASE),
)
_TOKENS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+\.\d+|\d+|'[^']*'|[(),.*=<>!+-/%|]")
_KEYWORDS = frozenset(
    {
        "with",
        "select",
        "from",
        "join",
        "left",
        "right",
        "inner",
        "outer",
        "full",
        "cross",
        "on",
        "where",
        "group",
        "by",
        "having",
        "order",
        "limit",
        "offset",
        "as",
        "and",
        "or",
        "is",
        "null",
        "case",
        "when",
        "then",
        "else",
        "end",
        "cast",
    }
)

_IDENTIFIER_PREFIX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_JOIN_INTRODUCER = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:(?:left|right|full|inner|cross)\s+)?(?:outer\s+)?join(?![A-Za-z0-9_])"
)
_ALIAS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class UpstreamDefinition:
    """One upstream model's compiled SQL, with its completeness from E2."""

    sql: str
    complete: bool = True


@dataclass(frozen=True)
class ColumnReference:
    """One side of the failing expression resolved to its source column."""

    reference: str
    relation: str
    column: str
    chain: tuple[str, ...] = ()


@dataclass(frozen=True)
class MappingResult:
    status: Literal["RESOLVED", "UNKNOWN"]
    expression: str | None = None
    references: tuple[ColumnReference, ...] = ()
    reason: str | None = None


def _unknown(reason: str, expression: str | None = None) -> MappingResult:
    assert reason in UNKNOWN_REASONS, reason
    return MappingResult(status="UNKNOWN", expression=expression, reason=reason)


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", " ", sql)


def normalize_sql_text(text: str) -> str:
    """Canonical text for *identification only* (never for mapping)."""

    return re.sub(r"\s+", " ", _strip_comments(text)).strip().lower()


def _unquote(identifier: str) -> str:
    parts = [part.strip().strip('"') for part in identifier.split(".")]
    return ".".join(part for part in parts if part)


def _last_identifier(identifier: str) -> str:
    parts = [part.strip().strip('"') for part in identifier.split(".")]
    parts = [part for part in parts if part]
    return parts[-1] if parts else ""


def _split_top_level(text: str, separator: str) -> list[str]:
    """Split on ``separator`` outside parentheses and quotes."""

    parts: list[str] = []
    depth = 0
    in_quote = False
    current: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            in_quote = not in_quote
            current.append(char)
        elif not in_quote and char == "(":
            depth += 1
            current.append(char)
        elif not in_quote and char == ")":
            depth -= 1
            current.append(char)
        elif not in_quote and depth == 0 and text.startswith(separator, index):
            parts.append("".join(current))
            current = []
            index += len(separator)
            continue
        else:
            current.append(char)
        index += 1
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


def _balanced_parens(text: str, start: int) -> int | None:
    depth = 0
    in_quote = False
    for index in range(start, len(text)):
        char = text[index]
        if char == "'":
            in_quote = not in_quote
        elif not in_quote and char == "(":
            depth += 1
        elif not in_quote and char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


@dataclass(frozen=True)
class _Select:
    projections: tuple[tuple[str, str | None], ...]
    sources: dict[str, str]
    joins: tuple[tuple[str, str], ...] = ()
    groups: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Query:
    ctes: dict[str, str]
    body: str


def _parse_query(sql: str) -> _Query | None:
    text = _strip_comments(sql).strip().rstrip(";").strip()
    if not re.match(r"(?is)^with\b", text):
        return _Query({}, text)
    index = len("with")
    ctes: dict[str, str] = {}
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        name_match = _IDENTIFIER_PREFIX.match(text, index)
        if name_match is None:
            return None
        name = name_match.group(0).lower()
        index = name_match.end()
        while index < len(text) and text[index].isspace():
            index += 1
        if not text.startswith("as", index):
            return None
        index += 2
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text) or text[index] != "(":
            return None
        end = _balanced_parens(text, index)
        if end is None:
            return None
        if name in ctes:
            return None
        ctes[name] = text[index + 1 : end]
        index = end + 1
        while index < len(text) and text[index].isspace():
            index += 1
        if text.startswith(",", index):
            index += 1
            continue
        break
    body = text[index:].strip()
    if not re.match(r"(?is)^select\b", body):
        return None
    return _Query(ctes, body)


def _parse_select(body: str) -> _Select | None:
    text = body.strip()
    if not re.match(r"(?is)^select\b", text):
        return None
    from_parts = _split_keyword(text, "from")
    if from_parts is None:
        return None
    projection_text, remainder = from_parts
    projections: list[tuple[str, str | None]] = []
    for item in _split_top_level(projection_text[len("select") :], ","):
        alias_parts = _split_keyword(item, "as")
        if alias_parts is not None:
            expression, alias = alias_parts
            alias_name = _last_identifier(alias.strip())
            if not alias_name:
                return None
            projections.append((expression.strip(), alias_name.lower()))
        else:
            projections.append((item.strip(), None))
    sources: dict[str, str] = {}
    joins: list[tuple[str, str]] = []
    groups: tuple[str, ...] = ()
    clause = remainder
    group_parts = _split_keyword(clause, "group by")
    if group_parts is not None:
        clause, group_text = group_parts
        groups = tuple(
            _last_identifier(item.strip()).lower()
            for item in _split_top_level(group_text, ",")
        )
    for part in _split_on_pattern(clause, _JOIN_INTRODUCER):
        yield_condition = _split_keyword(part, "on")
        source_text = part if yield_condition is None else yield_condition[0]
        condition = None if yield_condition is None else yield_condition[1]
        source_name = _source_name(source_text)
        if source_name is None:
            return None
        if not joins and not sources:
            sources[source_name.lower()] = source_name
        else:
            if condition is None:
                return None
            # Joined sources are part of the scope: qualified references may
            # name them, even though ``select *`` passthrough stays limited to
            # the single FROM source.
            sources[source_name.lower()] = source_name
            joins.append((source_name, condition.strip()))
    if not sources:
        return None
    return _Select(tuple(projections), sources, tuple(joins), groups)


def _source_name(text: str) -> str | None:
    """The relation or CTE name of one FROM/JOIN entry (alias supported)."""

    cleaned = text.strip()
    if not cleaned:
        return None
    tokens = cleaned.split()
    name = tokens[0].strip()
    if not name:
        return None
    if len(tokens) > 1 and tokens[1].lower() != "as" and not _ALIAS_PATTERN.match(tokens[1]):
        return None
    return _unquote(name)


def _split_keyword(text: str, keyword: str) -> tuple[str, str] | None:
    parts = _split_keyword_all(text, keyword)
    if len(parts) < 2:
        return None
    return parts[0], keyword.join(parts[1:])


def _split_keyword_all(text: str, keyword: str) -> list[str]:
    pattern = re.compile(rf"(?i)(?<![A-Za-z0-9_]){re.escape(keyword)}(?![A-Za-z0-9_])")
    return _split_on_pattern(text, pattern)


def _split_on_pattern(text: str, pattern: re.Pattern[str]) -> list[str]:
    parts: list[str] = []
    depth = 0
    in_quote = False
    last = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            in_quote = not in_quote
        elif not in_quote and char == "(":
            depth += 1
        elif not in_quote and char == ")":
            depth -= 1
        elif not in_quote and depth == 0:
            match = pattern.match(text, index)
            if match is not None:
                parts.append(text[last:index].strip())
                last = match.end()
                index = match.end()
                continue
        index += 1
    parts.append(text[last:].strip())
    return [part for part in parts if part]


def _split_reference(reference: str) -> tuple[str | None, str]:
    """a.b -> ("a", "b"); a bare column -> (None, column)."""

    if "." in reference:
        qualifier, _, column = reference.partition(".")
        return qualifier, column
    return None, reference


def _expression_references(expression: str) -> list[str] | None:
    """Identifiers one expression reads, or None when unsupported."""

    for pattern in _UNSUPPORTED_PATTERNS:
        if pattern.search(expression):
            return None
    references: list[str] = []
    cast_skip = False
    tokens = _TOKENS.findall(expression)
    for position, token in enumerate(tokens):
        lowered = token.lower()
        if lowered in {"(", ")", ",", "*", "."}:
            continue
        if token.startswith("'") or token[0].isdigit():
            continue
        if lowered in _EXPRESSION_KEYWORDS:
            cast_skip = lowered == "as" and any(
                item.lower() == "cast" for item in tokens[:position]
            )
            continue
        if cast_skip:
            # The type name right after `cast(... as <type>)`.
            cast_skip = False
            continue
        if not _ALIAS_PATTERN.match(token):
            continue
        if position + 2 < len(tokens) and tokens[position + 1] == ".":
            qualifier = token.lower()
            column = tokens[position + 2].lower()
            if not _ALIAS_PATTERN.match(tokens[position + 2]):
                return None
            references.append(f"{qualifier}.{column}")
        else:
            if position > 0 and tokens[position - 1] == ".":
                continue
            references.append(lowered)
    return list(dict.fromkeys(references))


class _Resolver:
    def __init__(self, upstream: Mapping[str, UpstreamDefinition]) -> None:
        self._upstream = upstream
        self._queries: dict[str, _Query | None] = {}

    def resolve(
        self,
        *,
        column: str,
        qualifier: str | None,
        select: _Select,
        ctes: Mapping[str, str],
        chain: tuple[str, ...],
        depth: int = 0,
    ) -> tuple[ColumnReference, ...] | str:
        """Resolve one reference to its origin column, or return a reason."""

        if depth > 8:
            return "DEPTH_EXCEEDED"
        candidates = [column]
        sources = select.sources
        if qualifier is not None:
            source = None
            for alias, name in sources.items():
                if alias == qualifier or _last_identifier(name).lower() == qualifier:
                    source = name
                    break
            if source is None:
                return "UNKNOWN_SOURCE"
            chosen = [(qualifier, source)]
        else:
            if len(sources) != 1:
                return "AMBIGUOUS_COLUMN"
            chosen = list(sources.items())
        results: list[ColumnReference] = []
        for alias, source in chosen:
            resolution = self._resolve_in_source(
                column=column,
                alias=alias,
                source=source,
                ctes=ctes,
                chain=chain,
                depth=depth,
            )
            if isinstance(resolution, str):
                return resolution
            results.extend(resolution)
        del candidates
        return tuple(results)

    def _resolve_in_source(
        self,
        *,
        column: str,
        alias: str,
        source: str,
        ctes: Mapping[str, str],
        chain: tuple[str, ...],
        depth: int,
    ) -> tuple[ColumnReference, ...] | str:
        lowered = source.lower()
        if lowered in ctes:
            inner = _parse_select(ctes[lowered])
            if inner is None:
                # `select *` chains and single-source CTEs are the supported
                # multi-CTE shapes; anything else is unsupported.
                inner = self._passthrough_select(ctes[lowered])
                if inner is None:
                    return "UNSUPPORTED_SELECT"
            return self._project(
                column=column,
                select=inner,
                ctes=ctes,
                chain=chain + (lowered,),
                depth=depth,
            )
        definition = self._upstream.get(source) or self._upstream.get(lowered)
        if definition is None:
            # The unqualified relation name is what E1 expectations and the
            # scenario contracts use; the full source stays in the chain.
            return (ColumnReference(
                reference=f"{alias}.{column}",
                relation=_last_identifier(source),
                column=column,
                chain=chain,
            ),)
        if not definition.complete:
            return "DEFINITION_INCOMPLETE"
        query = self._query_for(source, definition.sql)
        if query is None:
            return "UNSUPPORTED_SELECT"
        select = _parse_select(query.body)
        if select is None:
            passthrough = self._passthrough_select(query.body)
            if passthrough is None:
                return "UNSUPPORTED_SELECT"
            select = passthrough
        return self._project(
            column=column,
            select=select,
            ctes=query.ctes,
            chain=chain + (source,),
            depth=depth + 1,
        )

    def _project(
        self,
        *,
        column: str,
        select: _Select,
        ctes: Mapping[str, str],
        chain: tuple[str, ...],
        depth: int,
    ) -> tuple[ColumnReference, ...] | str:
        for expression, alias in select.projections:
            if expression.strip() == "*":
                return self.resolve(
                    column=column,
                    qualifier=None,
                    select=select,
                    ctes=ctes,
                    chain=chain,
                    depth=depth,
                )
            target_alias = alias if alias is not None else expression.strip().lower()
            if target_alias == column.lower():
                references = _expression_references(expression)
                if references is None:
                    return "UNSUPPORTED_EXPRESSION"
                if not references:
                    return "UNKNOWN_COLUMN"
                resolved: list[ColumnReference] = []
                for reference in references:
                    qualifier, name = _split_reference(reference)
                    outcome = self.resolve(
                        column=name,
                        qualifier=qualifier,
                        select=select,
                        ctes=ctes,
                        chain=chain,
                        depth=depth + 1,
                    )
                    if isinstance(outcome, str):
                        return outcome
                    resolved.extend(outcome)
                if len(resolved) != 1:
                    return "AMBIGUOUS_COLUMN"
                return tuple(resolved)
        return "UNKNOWN_COLUMN"

    @staticmethod
    def _passthrough_select(body: str) -> _Select | None:
        """``select * from <source>`` with no joins."""

        match = re.match(
            r"(?is)^select\s+\*\s+from\s+([A-Za-z_][\w.\"]*)\s*$", body.strip()
        )
        if match is None:
            return None
        source = _source_name(match.group(1))
        if source is None:
            return None
        return _Select((("*", None),), {source.lower(): source})

    def _query_for(self, source: str, sql: str) -> _Query | None:
        if source not in self._queries:
            self._queries[source] = _parse_query(sql)
        return self._queries[source]


def _candidates(query: _Query) -> list[tuple[str, _Select, Mapping[str, str]]]:
    """Every candidate expression with the select scope it lives in."""

    candidates: list[tuple[str, _Select, Mapping[str, str]]] = []
    for body, ctes in [
        *((cte_body, query.ctes) for cte_body in query.ctes.values()),
        (query.body, query.ctes),
    ]:
        select = _parse_select(body)
        if select is None:
            continue
        for expression, _alias in select.projections:
            if expression.strip() != "*":
                candidates.append((expression, select, ctes))
        for _source, condition in select.joins:
            candidates.append((condition, select, ctes))
    return candidates


@dataclass
class _Identified:
    expression: str
    select: _Select
    ctes: Mapping[str, str]
    references: tuple[str, ...] = field(default_factory=tuple)


def map_failing_expression(
    node_sql: str,
    message: str,
    *,
    upstream: Mapping[str, UpstreamDefinition] | None = None,
) -> MappingResult:
    """Map the failing expression to the source columns it reads.

    ``node_sql`` is the failing node's archived compiled SQL (complete), and
    ``upstream`` maps relation names to the definitions of the models the SQL
    reads (each carrying its E2 completeness).
    """

    definitions = dict(upstream or {})
    query = _parse_query(node_sql)
    if query is None:
        return _unknown("UNSUPPORTED_SELECT")
    normalized_message = normalize_sql_text(message)
    if not normalized_message:
        return _unknown("EXPRESSION_NOT_IDENTIFIED")
    matches: list[tuple[str, _Select, Mapping[str, str]]] = []
    for expression, select, ctes in _candidates(query):
        normalized = normalize_sql_text(expression)
        if not normalized:
            continue
        if normalized in normalized_message:
            matches.append((expression, select, ctes))
    if not matches:
        return _unknown("EXPRESSION_NOT_IDENTIFIED")
    # A hit that is a substring of another hit is the same expression seen
    # through a narrower candidate (for example the projection
    # ``customers.customer_id`` inside the join condition that names it); only
    # the maximal hits count, and two maximal hits stay ambiguous.
    normalized_hits = [normalize_sql_text(item[0]) for item in matches]
    maximal = [
        item
        for item, normalized in zip(matches, normalized_hits, strict=True)
        if not any(
            normalized != other and normalized in other
            for other in normalized_hits
        )
    ]
    if len(maximal) > 1:
        return _unknown("EXPRESSION_AMBIGUOUS")
    matches = maximal

    expression, select, ctes = matches[0]
    expression = _without_alias(expression)
    comparison = _split_comparison(expression)
    resolver = _Resolver(definitions)
    references: list[ColumnReference] = []
    sides = comparison if comparison is not None else (expression,)
    for side in sides:
        side_references = _expression_references(side)
        if side_references is None:
            return _unknown("UNSUPPORTED_EXPRESSION", expression)
        if not side_references:
            return _unknown("UNSUPPORTED_EXPRESSION", expression)
        for reference in side_references:
            qualifier, name = _split_reference(reference)
            outcome = resolver.resolve(
                column=name,
                qualifier=qualifier,
                select=select,
                ctes=ctes,
                chain=(),
            )
            if isinstance(outcome, str):
                return _unknown(outcome, expression)
            references.extend(outcome)
    unique: list[ColumnReference] = []
    for reference in references:
        if reference not in unique:
            unique.append(reference)
    return MappingResult(
        status="RESOLVED", expression=expression, references=tuple(unique)
    )


def _without_alias(expression: str) -> str:
    """Drop a trailing ``as <alias>`` so only the expression itself is scanned."""

    parts = _split_keyword(expression, "as")
    if parts is None:
        return expression
    head, tail = parts
    if _ALIAS_PATTERN.match(_last_identifier(tail.strip())):
        return head
    return expression


def _split_comparison(expression: str) -> tuple[str, str] | None:
    """``a = b`` (or ``<>``) at the top level, or None."""

    parts = _split_on_pattern(expression, re.compile(r"(?<![<>!=])(?:=|<>|!=)(?!=)"))
    if len(parts) != 2:
        return None
    return parts[0], parts[1]
