"""How a model uses a column on its way to an output column.

When a column's type changes to text, what happens to a model that reads it depends on what the
model does with it. A column that is only selected stays text and breaks nothing; `SUM(amount)`
or `amount / 100` on text makes the query fail. For every (source column, output column) pair of
a model, `column_uses` says which of three it is:

- PASS    the output is the column itself (an alias, a CASE branch or a COALESCE argument are
          still the column), so the output has the column's type
- STRICT  the column is the direct argument of SUM, AVG, date_diff or plain arithmetic, which a
          text value cannot be given to
- OTHER   anything else (a cast, ROUND, UPPER, COUNT, MIN, MAX, a comparison, a CASE condition):
          the output has some other type, or the use is not one we know to fail

Across the CTEs of one model the use closest to the table decides, unless it is PASS: a cast
before a SUM means the SUM sees the cast type. When one column reaches an output in several ways,
STRICT wins over OTHER and OTHER over PASS, the cautious order for finding failures.

Only value flow is covered: a column used in a WHERE or a JOIN feeds no output and has no entry.

Known limit: a CTE that is a UNION is not traced, so its pairs have no entry. Callers treat a
missing pair like OTHER.
"""

from sqlglot import exp
from sqlglot.optimizer.scope import Scope, build_scope

from schema_impact.core.lineage import ColumnRef, qualified_models
from schema_impact.core.registry import Project

PASS = "pass"
STRICT = "strict"
OTHER = "other"

_RANK = {PASS: 0, OTHER: 1, STRICT: 2}
_TRANSPARENT = "transparent"

# Functions and operators that a text value cannot be an argument of (measured on DuckDB).
_STRICT_NODES = (exp.Sum, exp.Avg, exp.DateDiff, exp.Add, exp.Sub, exp.Mul, exp.Div, exp.IntDiv)


def column_uses(project: Project) -> dict[tuple[ColumnRef, ColumnRef], str]:
    """The use of each source column in each output column: {(source, output): use}."""
    uses: dict[tuple[ColumnRef, ColumnRef], str] = {}
    for model, query, _ in qualified_models(project):
        scope = build_scope(query) if isinstance(query, exp.Select) else None
        if scope is None:
            continue
        for projection in query.selects:
            output = ColumnRef(model.name, projection.alias_or_name)
            for source, use in _projection_uses(projection, scope, {}).items():
                key = (source, output)
                uses[key] = _worse(uses[key], use) if key in uses else use
    return uses


def _projection_uses(
    projection: exp.Expression, scope: Scope, cache: dict[int, dict[ColumnRef, str]]
) -> dict[ColumnRef, str]:
    """The real columns that one select expression is made from, each with its use."""
    if id(projection) in cache:
        return cache[id(projection)]
    own_columns = {id(column) for column in scope.columns}  # not the columns of nested selects
    result: dict[ColumnRef, str] = {}
    for column in projection.find_all(exp.Column):
        if id(column) not in own_columns:
            continue
        hop = _hop_use(column, projection)
        for source, below in _sources_of(column, scope, cache).items():
            # the use closest to the table decides, unless the column was only passed on there
            use = hop if below == PASS else below
            result[source] = _worse(result[source], use) if source in result else use
    cache[id(projection)] = result
    return result


def _sources_of(
    column: exp.Column, scope: Scope, cache: dict[int, dict[ColumnRef, str]]
) -> dict[ColumnRef, str]:
    """The table columns `column` is made from, with the use inside the CTE or subquery it names."""
    source = scope.sources.get(column.table)
    if isinstance(source, exp.Table):
        return {ColumnRef(source.name, column.name): PASS}
    if not isinstance(source, Scope) or not isinstance(source.expression, exp.Select):
        return {}  # an unknown alias, or a CTE that is a UNION
    for projection in source.expression.selects:
        if projection.alias_or_name == column.name:
            return _projection_uses(projection, source, cache)
    return {}


def _hop_use(column: exp.Column, projection: exp.Expression) -> str:
    """How `column` is used inside one select expression."""
    node = column
    while node is not projection and node.parent is not None:
        kind = _classify(node.parent, node)
        if kind != _TRANSPARENT:
            return kind
        node = node.parent
    return PASS


def _classify(parent: exp.Expression, child: exp.Expression) -> str:
    """What `parent` makes of `child`: hands it on (transparent), needs a number, or neither."""
    if isinstance(parent, exp.Alias | exp.Paren | exp.Coalesce):
        return _TRANSPARENT
    if isinstance(parent, exp.Case):
        # a branch is a value; the operand of `CASE x WHEN ...` is compared
        return OTHER if child.arg_key == "this" else _TRANSPARENT
    if isinstance(parent, exp.If):
        return OTHER if child.arg_key == "this" else _TRANSPARENT  # the condition is compared
    if isinstance(parent, _STRICT_NODES):
        return STRICT
    if isinstance(parent, exp.Anonymous) and parent.name.lower() in ("date_diff", "datediff"):
        return STRICT
    return OTHER


def _worse(a: str, b: str) -> str:
    return a if _RANK[a] >= _RANK[b] else b
