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
- relations are matched by their **full identity** (quoting and case only):
  ``analytics.raw_orders`` and ``other.analytics.raw_orders`` never share a
  definition or a terminal declaration. Two spellings of one relation must be
  established by the caller from public run metadata (for example by listing
  both as keys of the same definition); unknown spellings stay UNKNOWN;
- the walk only ends at a relation the caller reports as a **terminal input**
  (a publicly known seed/source, by full identity). A relation with neither a
  definition nor a terminal status — for example a model whose definition was
  withheld — is an explicit UNKNOWN, never an origin;
- inside that subgraph only the documented shapes are supported (single-source
  alias projections, arithmetic/cast expressions, multi-CTE chains, two-source
  equi-joins between single-column sides, aggregates over grouped keys), and
  every expression must be consumed completely. Anything else — window
  functions, subqueries, non-equi or conjunctive join conditions, function calls
  outside the documented set, unsupported keywords or symbols — is an explicit
  UNKNOWN; a word is never counted as a column just because it could not be
  classified.

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

#: Keywords the token scanner may see inside an expression. Anything outside
#: this table (plus identifiers, literals, operators and the function set) makes
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
    }
)
#: Function names the scanner accepts. A name followed by ``(`` that is not
#: listed here is an unsupported construct, never a column reference.
_ALLOWED_FUNCTIONS = frozenset(
    {"cast", "coalesce", "nullif", "sum", "min", "max", "count", "avg"}
)
#: Bare words that introduce constructs this reader does not support. Seeing one
#: refuses the expression instead of counting the word as a column.
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "all",
        "any",
        "between",
        "collate",
        "cross",
        "distinct",
        "escape",
        "except",
        "exists",
        "extract",
        "filter",
        "following",
        "from",
        "group",
        "having",
        "ilike",
        "in",
        "intersect",
        "interval",
        "join",
        "lateral",
        "like",
        "limit",
        "natural",
        "offset",
        "on",
        "order",
        "over",
        "overlay",
        "partition",
        "position",
        "preceding",
        "range",
        "recursive",
        "row",
        "rows",
        "select",
        "similar",
        "some",
        "substring",
        "trim",
        "unbounded",
        "union",
        "using",
        "where",
        "window",
        "with",
    }
)
#: Punctuation and operators the scanner passes over. Anything else (``::``,
#: ``||``, ``#``, …) refuses the expression instead of being skipped.
_ALLOWED_OPERATORS = frozenset({"+", "-", "*", "/", "%", "=", "<", ">", "!"})
#: Constructs that are never supported inside an expression.
_UNSUPPORTED_PATTERNS = (
    re.compile(r"\bover\s*\(", re.IGNORECASE),
    re.compile(r"\(\s*select\b", re.IGNORECASE),
    re.compile(r"\bfilter\s*\(", re.IGNORECASE),
    re.compile(r"\bdistinct\b", re.IGNORECASE),
)
_TOKENS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+\.\d+|\d+|'[^']*'|[(),.*=<>!+-/%|]")
_ALIAS_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_IDENTIFIER_TEXT = r'(?:"(?:[^"]|"")*"|[A-Za-z_][A-Za-z0-9_$]*)'
_QUALIFIED_TEXT = rf"{_IDENTIFIER_TEXT}(?:\s*\.\s*{_IDENTIFIER_TEXT})*"
#: One identifier segment; group 1 is the text inside quotes (doubled quotes are
#: an escaped quote), group 2 a bare identifier.
_IDENTIFIER_PART = re.compile(r'\s*(?:"((?:[^"]|"")*)"|([A-Za-z_][A-Za-z0-9_$]*))')
#: A segment that needs no quotes when rendered.
_BARE_IDENTIFIER = re.compile(r"[a-z_][a-z0-9_$]*")
#: One FROM/JOIN entry: a relation, optionally aliased (``as`` optional).
_SOURCE_ENTRY_PATTERN = re.compile(
    rf"(?is)^\s*({_QUALIFIED_TEXT})(?:\s+as\s+({_IDENTIFIER_TEXT})|\s+({_IDENTIFIER_TEXT}))?\s*$"
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


def _last_identifier(identifier: str) -> str:
    parts = [part.strip().strip('"') for part in identifier.split(".")]
    parts = [part for part in parts if part]
    return parts[-1] if parts else ""


def _fold_identifier(match: re.Match[str]) -> str:
    """PostgreSQL identifier folding: bare names lowercase, quoted ones exact."""

    quoted = match.group(1)
    if quoted is None:
        return match.group(2).lower()
    return quoted.replace('""', '"')


def _relation_parts(reference: str) -> tuple[str, ...] | None:
    """Identifier segments of one relation reference, or None when malformed.

    Quoting is significant. A quoted segment keeps its exact text and a dot
    inside quotes is part of the name, so ``analytics."raw.customers"`` is a
    relation ``raw.customers`` in schema ``analytics`` — not three segments —
    and ``analytics."STG_CUSTOMERS"`` is not ``analytics.stg_customers``. Bar
    segments fold to lowercase, exactly as PostgreSQL resolves them.
    """

    text = reference.strip()
    if not text:
        return None
    parts: list[str] = []
    index = 0
    while True:
        match = _IDENTIFIER_PART.match(text, index)
        if match is None:
            return None
        parts.append(_fold_identifier(match))
        index = match.end()
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            break
        if text[index] != ".":
            return None
        index += 1
    return tuple(parts)


def _quote_part(part: str) -> str:
    return part if _BARE_IDENTIFIER.fullmatch(part) else '"' + part.replace('"', '""') + '"'


def _render_identity(parts: tuple[str, ...]) -> str:
    """Canonical text of one identity: segments re-quoted where needed, so
    distinct identifiers never render alike."""

    return ".".join(_quote_part(part) for part in parts)


def relation_identity(reference: str) -> str | None:
    """Public text of one relation identity, or None when malformed.

    The harness publishes this form (in the baseline snapshot and the node
    definition facts) so a caller can match an identity the tools reported
    against the identity the reader reports, without any name-similarity
    guessing: both sides go through the same segments-and-quoting rules.
    """

    parts = _relation_parts(reference)
    return None if parts is None else _render_identity(parts)


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
    sources: dict[str, tuple[str, ...]]
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
        name_match = _IDENTIFIER_PART.match(text, index)
        if name_match is None:
            return None
        name = _fold_identifier(name_match)
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


def _source_entry(text: str) -> tuple[str, tuple[str, ...]] | None:
    """``(alias, relation segments)`` of one FROM/JOIN entry, or None.

    The alias is the SQL-visible name: the written alias, or the relation's last
    segment. A written alias *replaces* the relation name, so the scope never
    keeps the original name as a second way in. The entry must be exactly a
    relation with an optional alias — trailing text (a comma-joined source, a
    ``union`` branch, an unparsed keyword) refuses the whole entry.
    """

    match = _SOURCE_ENTRY_PATTERN.match(text)
    if match is None:
        return None
    parts = _relation_parts(match.group(1))
    if parts is None:
        return None
    written = match.group(2) or match.group(3)
    if written is None:
        alias = parts[-1]
    else:
        written_parts = _relation_parts(written)
        if written_parts is None or len(written_parts) != 1:
            return None
        alias = written_parts[0]
    if not alias:
        return None
    # A quoted alias or quoted relation keeps its exact text, so the scope key
    # never matches a bare (lowercased) qualifier — as in PostgreSQL.
    return alias, parts


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
    """``(qualifier, column)`` of one bare column reference, or None.

    Quoted identifiers are refused: columns are matched lowercased here, and
    pretending ``"ID"`` were ``id`` would attribute the wrong column.
    """

    if '"' in text:
        return None
    parts = _relation_parts(text)
    if parts is None or len(parts) > 2:
        return None
    return (parts[0] if len(parts) == 2 else None), parts[-1]


#: Characters that extend a reference: a hit touching one of them is part of a
#: longer expression and is therefore not an identification.
_EXTENDING_CHARACTER = re.compile(r"[A-Za-z0-9_.$]")
#: How dbt/PostgreSQL mark a failing line they cut to a fixed width.
_TRUNCATION_MARKER = "..."


def _bounded_spans(normalized: str, message: str) -> list[tuple[int, int]]:
    """Spans where ``normalized`` occurs in ``message`` on its own boundaries.

    A candidate that only occurs *inside* a longer reference is not the
    expression the message names: ``customer_id`` inside
    ``customers.customer_id`` is a different expression, and matching it would
    map a substring of the failing SQL instead of the failing SQL.
    """

    spans: list[tuple[int, int]] = []
    start = message.find(normalized)
    while start != -1:
        end = start + len(normalized)
        before = message[start - 1] if start > 0 else ""
        after = message[end] if end < len(message) else ""
        if not _EXTENDING_CHARACTER.match(before) and not _EXTENDING_CHARACTER.match(after):
            spans.append((start, end))
        start = message.find(normalized, start + 1)
    return spans


def _truncation_spans(normalized: str, message: str) -> list[tuple[int, int]]:
    """Spans where a candidate appears only as a truncated prefix.

    dbt reports the failing line cut to a fixed width and marked with ``...``,
    so the tail of an expression never appears in the message (dry-run finding:
    the real message carries only 64 characters and cuts inside an identifier).
    A prefix ending exactly at such a marker is that expression — the mapping
    itself still uses the full SQL text, never the fragment. The marker must end
    a fragment (followed by whitespace or the end of the message): an ellipsis
    inside a literal is not a truncation.
    """

    spans: list[tuple[int, int]] = []
    index = message.find(_TRUNCATION_MARKER)
    while index != -1:
        following = message[index + len(_TRUNCATION_MARKER) :][:1]
        if following in {"", " "}:
            for length in range(min(index, len(normalized)), 0, -1):
                if message[index - length : index] == normalized[:length]:
                    start = index - length
                    before = message[start - 1] if start > 0 else ""
                    if not _EXTENDING_CHARACTER.match(before):
                        spans.append((start, index))
                    break
        index = message.find(_TRUNCATION_MARKER, index + 1)
    return spans


def _tokenize_expression(expression: str) -> list[str] | None:
    """Tokens of one expression, or None when a symbol is not recognised.

    The tokenizer is complete by construction: a gap between two tokens that is
    not whitespace means an unsupported symbol (``::``, ``||``, …) and refuses
    the expression instead of being skipped on the way to a confirmation.
    """

    tokens: list[str] = []
    position = 0
    for match in _TOKENS.finditer(expression):
        if expression[position : match.start()].strip():
            return None
        tokens.append(match.group(0))
        position = match.end()
    if expression[position:].strip():
        return None
    return tokens


def _expression_references(expression: str) -> list[str] | None:
    """Column references one expression reads, or None when unsupported.

    A word followed by ``(`` is a function call: only the documented function
    set is accepted, so an unknown function refuses the expression instead of
    being counted as a column. Everything else must be consumed as a reference,
    a literal, a known keyword or an allowed operator.
    """

    for pattern in _UNSUPPORTED_PATTERNS:
        if pattern.search(expression):
            return None
    tokens = _tokenize_expression(expression)
    if tokens is None:
        return None
    references: list[str] = []
    casts: list[int] = []
    type_skip_depth: int | None = None
    depth = 0
    index = 0
    while index < len(tokens):
        token = tokens[index]
        lowered = token.lower()
        if token == "(":
            depth += 1
            index += 1
            continue
        if token == ")":
            depth -= 1
            if depth < 0:
                return None
            if casts and depth < casts[-1]:
                casts.pop()
                type_skip_depth = None
            index += 1
            continue
        if type_skip_depth is not None:
            # Inside a cast, the target type (with its own modifiers) is not
            # part of what the expression reads.
            index += 1
            continue
        if token == ",":
            index += 1
            continue
        if token.startswith("'") or token[0].isdigit():
            index += 1
            continue
        if lowered in _EXPRESSION_KEYWORDS:
            if lowered == "as" and casts and casts[-1] == depth:
                type_skip_depth = depth
            index += 1
            continue
        if lowered in _UNSUPPORTED_KEYWORDS:
            return None
        if _ALIAS_PATTERN.match(token):
            following = tokens[index + 1] if index + 1 < len(tokens) else ""
            if following == "(":
                if lowered not in _ALLOWED_FUNCTIONS:
                    return None
                if lowered == "cast":
                    casts.append(depth + 1)
                index += 1
                continue
            if following == ".":
                if index + 2 >= len(tokens) or not _ALIAS_PATTERN.match(tokens[index + 2]):
                    return None
                column = tokens[index + 2].lower()
                if column in _EXPRESSION_KEYWORDS or column in _UNSUPPORTED_KEYWORDS:
                    return None
                references.append(f"{lowered}.{column}")
                index += 3
                continue
            if index > 0 and tokens[index - 1] == ".":
                index += 1
                continue
            references.append(lowered)
            index += 1
            continue
        if token not in _ALLOWED_OPERATORS:
            return None
        index += 1
    if depth != 0 or casts:
        return None
    return list(dict.fromkeys(references))


def _identity_of(reference: str, *, what: str) -> tuple[str, ...]:
    parts = _relation_parts(reference)
    if parts is None:
        raise ValueError(f"{what} is not a relation reference: {reference!r}")
    return parts


def _index_definitions(
    upstream: Mapping[str, UpstreamDefinition],
) -> dict[tuple[str, ...], UpstreamDefinition]:
    """Index definitions by full relation identity (segments, not text).

    The identity keeps its segments and its quoting: ``analytics.raw_orders``,
    ``analytics."raw.orders"`` and ``other.analytics.raw_orders`` are three
    different relations. Two keys with the same identity and different
    definitions are a caller error: the reader refuses to choose between them
    instead of silently picking one.
    """

    index: dict[tuple[str, ...], UpstreamDefinition] = {}
    for key, definition in upstream.items():
        identity = _identity_of(key, what="upstream definition key")
        if identity in index and index[identity] != definition:
            raise ValueError(
                f"duplicate upstream definition for relation {_render_identity(identity)!r}"
            )
        index[identity] = definition
    return index


def _index_terminals(relations: Collection[str]) -> frozenset[tuple[str, ...]]:
    return frozenset(
        _identity_of(relation, what="terminal relation") for relation in relations
    )


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
        source: tuple[str, ...],
        ctes: Mapping[str, str],
        chain: tuple[str, ...],
        depth: int,
    ) -> tuple[ColumnReference, ...] | str:
        rendered = _render_identity(source)
        if len(source) == 1 and source[0] in ctes:
            inner = _parse_select(ctes[source[0]])
            if inner is None:
                # `select *` chains and single-source CTEs are the supported
                # multi-CTE shapes; anything else is unsupported.
                inner = _passthrough_select(ctes[source[0]])
                if inner is None:
                    return "UNSUPPORTED_SELECT"
            return self._project(
                column=column,
                select=inner,
                ctes=ctes,
                chain=chain + (source[0],),
                depth=depth,
            )
        definition = self._upstream.get(source)
        if definition is None:
            if source not in self._terminals:
                # Not a declared seed/source: it may be a model whose
                # definition is missing, so it can never be an origin.
                return "DEFINITION_MISSING"
            return (
                ColumnReference(
                    reference=f"{alias}.{column}",
                    relation=rendered,
                    column=column,
                    chain=chain,
                ),
            )
        if not definition.complete:
            return "DEFINITION_INCOMPLETE"
        query = self._query_for(rendered, definition.sql)
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
            chain=chain + (rendered,),
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
    reads (each carrying its E2 completeness). Keys are matched by full relation
    identity — quoting and case only, never the last identifier alone; two keys
    with the same identity and different definitions raise ``ValueError``.

    ``terminal_relations`` names the relations publicly known to be inputs
    (seeds/sources), by the same full identity. Only those may end a walk: a
    relation with no definition and no terminal status is
    ``DEFINITION_MISSING``, never an origin. A relation listed in both places is
    walked (the definition is the richer statement); the terminal list only
    decides relations without one.
    """

    resolver = _Resolver(dict(upstream or {}), terminal_relations or ())
    query = _parse_query(node_sql)
    if query is None:
        return _unknown("UNSUPPORTED_SELECT")
    normalized_message = normalize_sql_text(message)
    if not normalized_message:
        return _unknown("EXPRESSION_NOT_IDENTIFIED")
    matches: list[tuple[str, str, _Select, Mapping[str, str], tuple[int, int]]] = []
    for kind, expression, select, ctes in _candidates(query):
        normalized = normalize_sql_text(expression)
        if not normalized:
            continue
        # The widest span this candidate is named by: its own text, or the
        # truncated prefix dbt leaves when it cuts the failing line.
        spans = _bounded_spans(normalized, normalized_message)
        spans += _truncation_spans(normalized, normalized_message)
        if spans:
            widest = max(spans, key=lambda span: span[1] - span[0])
            matches.append((kind, expression, select, ctes, widest))
    if not matches:
        return _unknown("EXPRESSION_NOT_IDENTIFIED")
    # A hit that lies inside another hit is the same expression seen through a
    # narrower candidate (the projection ``customers.customer_id`` inside the
    # join condition that names it, or inside a truncated rendering of it); only
    # the maximal spans count, and two maximal hits stay ambiguous.
    maximal = [
        item
        for item in matches
        if not any(
            other is not item
            and other[4] != item[4]
            and other[4][0] <= item[4][0]
            and item[4][1] <= other[4][1]
            for other in matches
        )
    ]
    if len(maximal) > 1:
        return _unknown("EXPRESSION_AMBIGUOUS")

    kind, expression, select, ctes, _span = maximal[0]
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
