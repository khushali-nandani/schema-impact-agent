"""Which output columns of a model changed when its SQL was edited.

`changed_outputs(before, after, ...)` compares two versions of one model and answers with

- a set of output columns: only these are computed differently (an empty set: the edit changed
  nothing that matters, such as a renamed CTE or different spacing)
- None: the edit cannot be narrowed down, so the whole model must be treated as changed

How it decides. Both versions are qualified against their schema, so every column says which table
or CTE it comes from. Each CTE, and the final select, is a scope. A scope is compared on its
projections, one by one, and on its "frame", everything but the select list: FROM, JOIN, WHERE,
GROUP BY, HAVING, QUALIFY, DISTINCT, ORDER BY and LIMIT. CTE names are made positional first, so
renaming one is not a change.

The change is then followed from scope to scope, in order:

- a changed frame changes which rows the scope has, so every column of it
- a scope that reads another one whose rows changed has changed rows too
- a column that is computed differently, or that is computed from a column that is, is changed
- a changed column that a later scope reads in its frame (a join key, a filter, a grouping) changes
  that scope's rows, hence all its columns; one it only selects or aggregates changes only the
  columns made from it

If the rows of the final select changed, the answer is None. So it is for anything this does not
understand: a different set or order of output columns, a CTE added or removed, a UNION, SQL that
does not parse or resolve. A subquery inside a select list or a frame sees the changes of every
scope before it, which can only add to the result. A wrong narrowing would hide a real change,
and None is what the tool answered before this existed.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from schema_impact.core.registry import Schema

_NOT_FRAME = {"expressions", "with", "with_"}  # what is not the frame of a select


def changed_outputs(
    before_sql: str, after_sql: str, before_schema: Schema, after_schema: Schema, dialect: str
) -> set[str] | None:
    """The output columns whose computation changed, or None if the edit cannot be narrowed."""
    try:
        before = _read(before_sql, before_schema, dialect)
        after = _read(after_sql, after_schema, dialect)
    except SqlglotError:
        return None
    if before is None or after is None or len(before) != len(after):
        return None

    changed_columns: list[set[str]] = []  # per scope, the columns computed differently
    changed_rows: list[bool] = []  # per scope, whether the set of rows is different
    for position, (old, new) in enumerate(zip(before, after, strict=True)):
        if old.names != new.names:
            return None
        columns = {name for name in old.names if old.projections[name] != new.projections[name]}
        rows = old.frame != new.frame

        upstream_changed = any(changed_rows[k] or changed_columns[k] for k in range(position))
        # a scope that reads a scope with different rows, or reads a column that is different in
        # its frame, has different rows too
        rows = rows or any(changed_rows[k] for k in new.reads)
        rows = rows or any(c in changed_columns[k] for k, c in new.frame_columns)
        rows = rows or (new.frame_has_subquery and upstream_changed)
        for name in new.names:
            read = new.projection_columns[name]
            has_subquery = new.projection_has_subquery[name]
            if any(c in changed_columns[k] for k, c in read) or (has_subquery and upstream_changed):
                columns.add(name)
        if rows:
            columns = set(old.names)
        changed_columns.append(columns)
        changed_rows.append(rows)

    return None if changed_rows[-1] else changed_columns[-1]


class _Scope:
    """One CTE or the final select, as text with the CTE names made positional."""

    def __init__(self) -> None:
        self.names: list[str] = []
        self.projections: dict[str, str] = {}
        self.frame = ""
        self.reads: set[int] = set()  # the scopes before it that it reads
        self.projection_columns: dict[str, set[tuple[int, str]]] = {}
        self.projection_has_subquery: dict[str, bool] = {}
        self.frame_columns: set[tuple[int, str]] = set()
        self.frame_has_subquery = False


def _read(sql: str, schema: Schema, dialect: str) -> list[_Scope] | None:
    """The scopes of a model's query in order, the final select last; None if not a plain SELECT."""
    query = qualify(sqlglot.parse_one(sql, read=dialect), schema=schema, dialect=dialect)
    if not isinstance(query, exp.Select):
        return None
    all_scopes = traverse_scope(query)
    kept = [scope for scope in all_scopes if scope.is_cte or scope.is_root]
    if not all(isinstance(scope.expression, exp.Select) for scope in kept):
        return None  # a CTE that is a UNION
    position = {id(scope): i for i, scope in enumerate(kept)}

    scopes = [_describe(scope, position) for scope in kept]  # needs the original names
    _make_names_positional(all_scopes, kept, position)
    for described, scope in zip(scopes, kept, strict=True):
        described.names = [p.alias_or_name for p in scope.expression.selects]
        described.projections = {
            p.alias_or_name: p.sql(dialect=dialect, comments=False)
            for p in scope.expression.selects
        }
        frame = scope.expression.copy()
        for key in _NOT_FRAME:
            frame.set(key, None)
        frame.set("expressions", [exp.Star()])
        described.frame = frame.sql(dialect=dialect, comments=False)
    return scopes


def _describe(scope: Scope, position: dict[int, int]) -> _Scope:
    """What a scope reads, by position, found before any name is changed."""
    described = _Scope()
    select = scope.expression
    described.reads = {
        position[id(source)]
        for source in scope.sources.values()
        if isinstance(source, Scope) and id(source) in position
    }
    for projection in select.selects:
        columns, subquery = _reads_of(projection, scope, position)
        described.projection_columns[projection.alias_or_name] = columns
        described.projection_has_subquery[projection.alias_or_name] = subquery
    for key, value in select.args.items():
        if key in _NOT_FRAME or value is None:
            continue
        for node in value if isinstance(value, list) else [value]:
            columns, subquery = _reads_of(node, scope, position)
            described.frame_columns |= columns
            described.frame_has_subquery = described.frame_has_subquery or subquery
    return described


def _reads_of(
    node: exp.Expression, scope: Scope, position: dict[int, int]
) -> tuple[set[tuple[int, str]], bool]:
    """The (scope, column) pairs read by an expression, and whether it holds a subquery."""
    columns: set[tuple[int, str]] = set()
    for column in node.find_all(exp.Column):
        source = scope.sources.get(column.table)
        if isinstance(source, Scope) and id(source) in position:
            columns.add((position[id(source)], column.name))
    subquery = any(inner is not node for inner in node.find_all(exp.Select))
    return columns, subquery


def _make_names_positional(
    all_scopes: list[Scope], kept: list[Scope], position: dict[int, int]
) -> None:
    """Replace every reference to a CTE by `cte<position>`, in the tree itself.

    Each reference is resolved in its own scope, so a real table that happens to be called like a
    CTE defined later is left alone. When the CTE is used under its own name (qualify writes
    `FROM final AS final`) the alias is renamed with it; an alias that is not the CTE's name is
    left as written.
    """
    ctes = {
        id(scope): (scope.expression.parent.alias, f"cte{position[id(scope)]}")
        for scope in kept
        if scope.is_cte
    }
    for scope in all_scopes:
        # how this scope calls each CTE it reads -> (the CTE's own name, its positional name)
        used = {
            alias: ctes[id(source)]
            for alias, source in scope.sources.items()
            if isinstance(source, Scope) and id(source) in ctes
        }
        for table in scope.tables:
            if table.alias_or_name not in used or table.args.get("db"):
                continue
            original, positional = used[table.alias_or_name]
            table.set("this", exp.to_identifier(positional))
            table_alias = table.args.get("alias")
            if table_alias is not None and table_alias.name == original:
                table_alias.set("this", exp.to_identifier(positional))
        for column in scope.columns:
            if column.table in used and used[column.table][0] == column.table:
                column.set("table", exp.to_identifier(used[column.table][1]))
