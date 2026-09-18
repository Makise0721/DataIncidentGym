"""T13 slice 2: the narrow column-mapping reader (design §2.2).

The reader answers one question: **which source relation and column does the
failing expression actually read?** It is deliberately narrow:

- the failing expression is *identified* from the node error message by finding
  exactly one candidate expression (a projection or a join condition of the
  node's SQL) whose normalized text occurs in the message **on its own
  boundaries** — a hit inside a longer reference (``customer_id`` inside
  ``customers.customer_id``) is a different expression and never an
  identification; zero or several matches is an explicit UNKNOWN. Matching text
  is only used to select the expression, never to map a column by similarity;
- only the **dependency subgraph of that expression** is parsed: the CTE/alias
  chains the expression's two sides reference. Unrelated joins, aggregations or
  clauses elsewhere in the same SQL are ignored and never trigger UNKNOWN;
- every select the walk *traverses* must be consumed as a whole by the
  documented grammar. A set operator (``union``/``intersect``/``except``), a
  comma-joined source, a join without ``on``, an entry that is not a plain
  relation name with an optional alias, or a clause in an impossible position
  all refuse the parse — the reader never keeps a recognizable prefix and drops
  the rest;
- relation scope follows SQL semantics: a written alias *replaces* the relation
  name. ``from raw_orders as raw_customers`` resolves the qualifier
  ``raw_customers`` to ``raw_orders`` and refuses the qualifier ``raw_orders``;
- the walk only ends at a relation the caller reports as a **terminal input**
  (a publicly known seed/source). A relation with neither a definition nor a
  terminal status — for example a model whose definition was withheld — is an
  explicit UNKNOWN, never an origin;
- inside that subgraph only the documented shapes are supported (single-source
  alias projections, arithmetic/cast expressions, multi-CTE chains, two-source
  equi-joins between single-column sides, aggregates over grouped keys).
  Anything else — window functions, subqueries, non-equi or conjunctive join
  conditions, functions the keyword table does not know, deeper nesting — is an
  explicit UNKNOWN.

UNKNOWN never counts as evidence in any direction. Definitions that are not
``complete`` (truncated or redaction-changed, per the E2 fact) must not be
passed in as usable: the caller marks them and the reader refuses to resolve
through them.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
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
        "DEFINITION_MISSING",
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
_ALIAS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENTIFIER_TEXT = r'(?:"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)'
_QUALIFIED_TEXT = rf"{_IDENTIFIER_TEXT}(?:\s*\.\s*{_IDENTIFIER_TEXT})*"
#: One FROM/JOIN entry: a relation, optionally aliased (``as`` optional).
_SOURCE_ENTRY_PATTERN = re.compile(
    rf"(?is)^\s*({_QUALIFIED_TEXT})(?:\s+as\s+({_IDENTIFIER_TEXT})|\s+({_IDENTIFIER_TEXT}))?\s*$"
)
#: One column reference: an optional qualifier and a column.
_REFERENCE_PATTERN = re.compile(
    rf"(?is)^\s*(?:({_IDENTIFIER_TEXT})\s*\.\s*)?({_IDENTIFIER_TEXT})\s*$"
)
#: Clause keywords that may follow the FROM/JOIN part of one select.
_SECTION_PATTERN = re.compile(
    r"(?i)(?<![A-Za-z0-9_])(?:"
    r"(?:(?:left|right|full|inner|cross)\s+)?(?:outer\s+)?join"
    r"|on|where|group\s+by|having|order\s+by|limit|offset"
    r"|union(?:\s+(?:all|distinct))?|intersect|except"
    r")(?![A-Za-z0-9_])"
)
#: The only clauses allowed after the FROM/JOIN part, in this order.
_TAIL_CLAUSES = ("where", "group by", "having", "order by", "limit", "offset")
#: A single top-level ``=`` (never ``<=``, ``>=``, ``!=`` or ``==``).
_EQUALITY_PATTERN = re.compile(r"(?<![<>!=])=(?!=)")


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


def _find_top_level(text: str, pattern: re.Pattern[str]) -> re.Match[str] | None:
    """First match of ``pattern`` outside parentheses and quotes."""

    depth = 0
    in_quote = False
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
                return match
        index += 1
    return None


def _split_keyword(text: str, keyword: str) -> tuple[str, str] | None:
    """Split at the first top-level occurrence of one keyword."""

    body = r"\s+".join(re.escape(part) for part in keyword.split())
    match = _find_top_level(
        text, re.compile(rf"(?i)(?<![A-Za-z0-9_]){body}(?![A-Za-z0-9_])")
    )
    if match is None:
        return None
    return text[: match.start()].strip(), text[match.end() :].strip()


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
        name_match = re.match(_IDENTIFIER_TEXT, text[index:])
        if name_match is None:
            return None
        name = name_match.group(0).lower()
        index += name_match.end()
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


def _source_entry(text: str) -> tuple[str, str] | None:
    """``(alias, relation)`` of one FROM/JOIN entry, or None when unsupported.

    The alias is the SQL-visible name: the written alias, or the relation's last
    identifier. A written alias *replaces* the relation name, so the scope never
    keeps the original name as a second way in. The entry must be exactly a
    relation with an optional alias — trailing text (a comma-joined source, a
    ``union`` branch, an unparsed keyword) refuses the whole entry.
    """

    match = _SOURCE_ENTRY_PATTERN.match(text)
    if match is None:
        return None
    relation = _unquote(match.group(1))
    written = match.group(2) or match.group(3)
    alias = (written or _last_identifier(relation)).strip().strip('"')
    if not _ALIAS_PATTERN.match(alias):
        return None
    return alias.lower(), relation


def _scan_sections(text: str) -> list[tuple[str, str]]:
    """Split a select tail into ``(keyword, body)`` segments at top level.

    The first segment is the FROM entry (keyword ``""``); every other segment
    starts at a clause keyword, normalised to single spaces. Parentheses and
    string literals are honoured, so a nested select never splits its enclosing
    section and never leaks into the outer one.
    """

    sections: list[tuple[str, str]] = []
    keyword = ""
    start = 0
    depth = 0
    in_quote = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            in_quote = not in_quote
            index += 1
            continue
        if not in_quote:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif depth == 0:
                match = _SECTION_PATTERN.match(text, index)
                if match is not None:
                    sections.append((keyword, text[start:index].strip()))
                    keyword = " ".join(match.group(0).lower().split())
                    if keyword.endswith("join"):
                        # left/right/full/inner/cross (outer) join are one kind
                        # of clause; their modifiers do not change the entry.
                        keyword = "join"
                    elif keyword.startswith("union"):
                        keyword = "union"
                    index = match.end()
                    start = index
                    continue
        index += 1
    sections.append((keyword, text[start:].strip()))
    return sections


def _parse_projections(text: str) -> tuple[tuple[str, str | None], ...] | None:
    body = text.strip()
    if not re.match(r"(?is)^select\b", body):
        return None
    items = _split_top_level(body[len("select") :], ",")
    if not items:
        return None
    projections: list[tuple[str, str | None]] = []
    for item in items:
        alias_parts = _split_keyword(item, "as")
        if alias_parts is None:
            projections.append((item.strip(), None))
            continue
        expression, alias = alias_parts
        alias_name = _last_identifier(alias)
        if not expression.strip() or not _ALIAS_PATTERN.match(alias_name):
            return None
        projections.append((expression.strip(), alias_name.lower()))
    return tuple(projections)


def _parse_group_items(text: str) -> tuple[str, ...] | None:
    """Group keys must be plain column references; anything else is refused."""

    groups: list[str] = []
    for item in _split_top_level(text, ","):
        references = _expression_references(item)
        if references is None or len(references) != 1:
            return None
        groups.append(references[0].split(".")[-1])
    return tuple(groups) if groups else None


def _parse_select(body: str) -> _Select | None:
    split = _split_keyword(body.strip(), "from")
    if split is None:
        return None
    projections = _parse_projections(split[0])
    if projections is None:
        return None
    sections = _scan_sections(split[1])
    entry = _source_entry(sections[0][1])
    if entry is None:
        return None
    sources: dict[str, str] = {entry[0]: entry[1]}
    joins: list[tuple[str, str]] = []
    groups: tuple[str, ...] = ()
    tail = -1
    index = 1
    while index < len(sections):
        keyword, piece = sections[index]
        if keyword == "join":
            if tail >= 0:
                return None
            joined = _source_entry(piece)
            if joined is None or joined[0] in sources:
                return None
            if index + 1 >= len(sections) or sections[index + 1][0] != "on":
                return None
            condition = sections[index + 1][1]
            if not condition:
                return None
            sources[joined[0]] = joined[1]
            joins.append((joined[0], condition))
            index += 2
            continue
        if keyword not in _TAIL_CLAUSES:
            # Set operators and unknown keywords end the supported grammar.
            return None
        position = _TAIL_CLAUSES.index(keyword)
        if position <= tail or not piece:
            return None
        tail = position
        if keyword == "group by":
            parsed_groups = _parse_group_items(piece)
            if parsed_groups is None:
                return None
            groups = parsed_groups
        index += 1
    return _Select(projections, sources, tuple(joins), groups)


def _split_reference(reference: str) -> tuple[str | None, str]:
    """a.b -> ("a", "b"); a bare column -> (None, column)."""

    if "." in reference:
        qualifier, _, column = reference.partition(".")
        return qualifier, column
    return None, reference


def _single_reference(text: str) -> tuple[str | None, str] | None:
    """``(qualifier, column)`` when the text is exactly one column reference."""

    match = _REFERENCE_PATTERN.match(text)
    if match is None:
        return None
    qualifier = match.group(1)
    return (
        None if qualifier is None else qualifier.strip().strip('"').lower(),
        match.group(2).strip().strip('"').lower(),
    )


#: Characters that extend a reference: a hit touching one of them is part of a
#: longer expression and is therefore not an identification.
_EXTENDING_CHARACTER = re.compile(r"[A-Za-z0-9_.$]")


def _bounded_occurrence(normalized: str, message: str) -> bool:
    """True when ``normalized`` occurs in ``message`` on its own boundaries.

    A candidate that only occurs *inside* a longer reference is not the
    expression the message names: ``customer_id`` inside
    ``customers.customer_id`` is a different expression, and matching it would
    map a substring of the failing SQL instead of the failing SQL.
    """

    start = message.find(normalized)
    while start != -1:
        end = start + len(normalized)
        before = message[start - 1] if start > 0 else ""
        after = message[end] if end < len(message) else ""
        if not _EXTENDING_CHARACTER.match(before) and not _EXTENDING_CHARACTER.match(after):
            return True
        start = message.find(normalized, start + 1)
    return False


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


def _index_definitions(
    upstream: Mapping[str, UpstreamDefinition],
) -> dict[str, UpstreamDefinition]:
    """Index definitions by their unqualified relation name.

    Two keys that normalise to the same relation name are a caller error: the
    reader refuses to choose between them instead of silently picking one.
    """

    index: dict[str, UpstreamDefinition] = {}
    for key, definition in upstream.items():
        name = _last_identifier(key).lower()
        if not name:
            raise ValueError("upstream definition key has no relation name")
        if name in index and index[name] != definition:
            raise ValueError(f"duplicate upstream definition for relation {name!r}")
        index[name] = definition
    return index


def _index_terminals(relations: Collection[str]) -> frozenset[str]:
    names = {_last_identifier(relation).lower() for relation in relations}
    if "" in names:
        raise ValueError("terminal relation name is empty")
    return frozenset(names)


class _Resolver:
    def __init__(
        self,
        upstream: Mapping[str, UpstreamDefinition],
        terminals: Collection[str],
    ) -> None:
        self._upstream = _index_definitions(upstream)
        self._terminals = _index_terminals(terminals)
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
        sources = select.sources
        if qualifier is not None:
            source = sources.get(qualifier)
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
        name = _last_identifier(source).lower()
        if "." not in source and name in ctes:
            inner = _parse_select(ctes[name])
            if inner is None:
                # `select *` chains and single-source CTEs are the supported
                # multi-CTE shapes; anything else is unsupported.
                inner = _passthrough_select(ctes[name])
                if inner is None:
                    return "UNSUPPORTED_SELECT"
            return self._project(
                column=column,
                select=inner,
                ctes=ctes,
                chain=chain + (name,),
                depth=depth,
            )
        definition = self._upstream.get(name)
        if definition is None:
            if name not in self._terminals:
                # Not a declared seed/source: it may be a model whose
                # definition is missing, so it can never be an origin.
                return "DEFINITION_MISSING"
            return (
                ColumnReference(
                    reference=f"{alias}.{column}",
                    relation=name,
                    column=column,
                    chain=chain,
                ),
            )
        if not definition.complete:
            return "DEFINITION_INCOMPLETE"
        query = self._query_for(name, definition.sql)
        if query is None:
            return "UNSUPPORTED_SELECT"
        select = _parse_select(query.body)
        if select is None:
            select = _passthrough_select(query.body)
            if select is None:
                return "UNSUPPORTED_SELECT"
        return self._project(
            column=column,
            select=select,
            ctes=query.ctes,
            chain=chain + (name,),
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

    def _query_for(self, name: str, sql: str) -> _Query | None:
        if name not in self._queries:
            self._queries[name] = _parse_query(sql)
        return self._queries[name]


def _passthrough_select(body: str) -> _Select | None:
    """``select * from <source>`` with no joins."""

    match = re.match(r"(?is)^select\s+\*\s+from\s+(.+)$", body.strip())
    if match is None:
        return None
    entry = _source_entry(match.group(1))
    if entry is None:
        return None
    return _Select((("*", None),), {entry[0]: entry[1]})


def _candidates(query: _Query) -> list[tuple[str, str, _Select, Mapping[str, str]]]:
    """Every candidate expression with its kind and the scope it lives in."""

    candidates: list[tuple[str, str, _Select, Mapping[str, str]]] = []
    for body, ctes in [
        *((cte_body, query.ctes) for cte_body in query.ctes.values()),
        (query.body, query.ctes),
    ]:
        select = _parse_select(body)
        if select is None:
            continue
        for expression, _alias in select.projections:
            if expression.strip() != "*":
                candidates.append(("projection", expression, select, ctes))
        for _alias, condition in select.joins:
            candidates.append(("join", condition, select, ctes))
    return candidates


def _reference_origins(
    resolver: _Resolver,
    *,
    references: tuple[str, ...],
    select: _Select,
    ctes: Mapping[str, str],
) -> tuple[ColumnReference, ...] | str:
    resolved: list[ColumnReference] = []
    for reference in references:
        qualifier, name = _split_reference(reference)
        outcome = resolver.resolve(
            column=name,
            qualifier=qualifier,
            select=select,
            ctes=ctes,
            chain=(),
        )
        if isinstance(outcome, str):
            return outcome
        resolved.extend(outcome)
    return tuple(resolved)


def map_failing_expression(
    node_sql: str,
    message: str,
    *,
    upstream: Mapping[str, UpstreamDefinition] | None = None,
    terminal_relations: Collection[str] | None = None,
) -> MappingResult:
    """Map the failing expression to the source columns it reads.

    ``node_sql`` is the failing node's archived compiled SQL (complete), and
    ``upstream`` maps relation names to the definitions of the models the SQL
    reads (each carrying its E2 completeness). Keys are normalised to their
    unqualified relation name; two keys sharing that name raise ``ValueError``.

    ``terminal_relations`` names the relations publicly known to be inputs
    (seeds/sources). Only those may end a walk: a relation with no definition
    and no terminal status is ``DEFINITION_MISSING``, never an origin. A
    relation listed in both places is walked (the definition is the richer
    statement); the terminal list only decides relations without one.
    """

    resolver = _Resolver(dict(upstream or {}), terminal_relations or ())
    query = _parse_query(node_sql)
    if query is None:
        return _unknown("UNSUPPORTED_SELECT")
    normalized_message = normalize_sql_text(message)
    if not normalized_message:
        return _unknown("EXPRESSION_NOT_IDENTIFIED")
    matches: list[tuple[str, str, _Select, Mapping[str, str]]] = []
    for kind, expression, select, ctes in _candidates(query):
        normalized = normalize_sql_text(expression)
        if not normalized:
            continue
        if _bounded_occurrence(normalized, normalized_message):
            matches.append((kind, expression, select, ctes))
    if not matches:
        return _unknown("EXPRESSION_NOT_IDENTIFIED")
    # A hit that is a substring of another hit is the same expression seen
    # through a narrower candidate (for example the projection
    # ``customers.customer_id`` inside the join condition that names it); only
    # the maximal hits count, and two maximal hits stay ambiguous.
    normalized_hits = [normalize_sql_text(item[1]) for item in matches]
    maximal = [
        item
        for item, normalized in zip(matches, normalized_hits, strict=True)
        if not any(
            normalized != other and normalized in other for other in normalized_hits
        )
    ]
    if len(maximal) > 1:
        return _unknown("EXPRESSION_AMBIGUOUS")

    kind, expression, select, ctes = maximal[0]
    expression = _without_alias(expression)
    if kind == "join":
        # Only a single equi-join condition between two column references is a
        # documented shape; `>`, `<>`, conjunctions and qualified expressions
        # must not be split into a half-read pair.
        comparison = _split_equality(expression)
        if comparison is None:
            return _unknown("UNSUPPORTED_EXPRESSION", expression)
        sides: list[str] = []
        for side in comparison:
            reference = _single_reference(side)
            if reference is None:
                return _unknown("UNSUPPORTED_EXPRESSION", expression)
            qualifier, name = reference
            sides.append(name if qualifier is None else f"{qualifier}.{name}")
        references: tuple[str, ...] = tuple(sides)
    else:
        scanned = _expression_references(expression)
        if not scanned:
            return _unknown("UNSUPPORTED_EXPRESSION", expression)
        references = tuple(scanned)
    origins = _reference_origins(resolver, references=references, select=select, ctes=ctes)
    if isinstance(origins, str):
        return _unknown(origins, expression)
    unique: list[ColumnReference] = []
    for reference in origins:
        if reference not in unique:
            unique.append(reference)
    return MappingResult(status="RESOLVED", expression=expression, references=tuple(unique))


def _without_alias(expression: str) -> str:
    """Drop a trailing ``as <alias>`` so only the expression itself is scanned."""

    parts = _split_keyword(expression, "as")
    if parts is None:
        return expression
    head, tail = parts
    if _ALIAS_PATTERN.match(_last_identifier(tail)):
        return head
    return expression


def _split_equality(expression: str) -> tuple[str, str] | None:
    """``a = b`` with exactly one top-level ``=``, or None."""

    parts = _split_top_level_on(expression, _EQUALITY_PATTERN)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _split_top_level_on(text: str, pattern: re.Pattern[str]) -> list[str]:
    """Split on every top-level match of ``pattern``."""

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
