"""Column-level lineage: which source columns a model depends on, and how.

Edge kinds:
- select:   the source column feeds one output column of the model
- join:     the source column is used in a JOIN condition (affects the whole model)
- where:    the source column is used in a WHERE filter (affects the whole model)
- group_by: the source column is used in GROUP BY (affects the whole model)
- having:   the source column is used in HAVING (affects the whole model)
- order_by: the source column is used in the query's ORDER BY (affects the whole model)
"""

from dataclasses import dataclass
from graphlib import CycleError, TopologicalSorter

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.lineage import Node, lineage
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope, traverse_scope

from schema_impact.core.registry import Model, Project, Schema

SELECT = "select"
JOIN = "join"
WHERE = "where"
GROUP_BY = "group_by"
HAVING = "having"
ORDER_BY = "order_by"


@dataclass(frozen=True)
class ColumnRef:
    """A column of a table or model, e.g. orders.amount.

    `column` is None when the reference is the whole model rather than one column.
    """

    table: str
    column: str | None


@dataclass(frozen=True)
class Edge:
    """`source` is used by `target`, in the way described by `kind`."""

    source: ColumnRef
    target: ColumnRef
    kind: str


class CircularDependencyError(ValueError):
    """Raised when models read each other in a cycle (a reads b, b reads a)."""


# Output columns of a model have no known type yet; lineage only needs the column names.
UNKNOWN_TYPE = "UNKNOWN"


def build_lineage(project: Project) -> list[Edge]:
    """Return every lineage edge for every model in the project.

    Models are processed in dependency order. Once a model is done, its output columns are
    added to the schema as if it were a table, so models that read it can be resolved.
    """
    schema = {table: dict(columns) for table, columns in project.schema.items()}
    edges: list[Edge] = []

    for model in _in_dependency_order(project):
        query = _qualified(model, schema, project.dialect)
        edges.extend(_select_edges(model, query, schema, project.dialect))
        edges.extend(_clause_edges(model, query))
        schema[model.name] = dict.fromkeys(query.named_selects, UNKNOWN_TYPE)

    return edges


def model_outputs(project: Project) -> dict[str, list[str]]:
    """The output columns of every model, in the order they are selected."""
    schema = {table: dict(columns) for table, columns in project.schema.items()}
    outputs: dict[str, list[str]] = {}
    for model in _in_dependency_order(project):
        columns = list(_qualified(model, schema, project.dialect).named_selects)
        outputs[model.name] = columns
        schema[model.name] = dict.fromkeys(columns, UNKNOWN_TYPE)
    return outputs


@dataclass(frozen=True)
class TopSelect:
    """The final SELECT of a model, split into what it outputs and everything else."""

    names: list[str]  # output columns, in order
    projections: dict[str, str]  # output column -> its expression, without the alias
    body: str  # the whole query with the final select list left out

    def __hash__(self) -> int:
        return hash((tuple(self.names), self.body))


def analyse_top_select(sql: str, schema: Schema, dialect: str) -> TopSelect | None:
    """Split a model's query into its final select list and the rest.

    None when that is not possible: the query does not resolve against the schema (it reads a
    column that is gone), it is not a plain SELECT, or a `*` cannot be expanded. Callers must
    then assume that anything may have changed.
    """
    try:
        query = qualify(sqlglot.parse_one(sql, read=dialect), schema=schema, dialect=dialect)
    except SqlglotError:
        return None
    if not isinstance(query, exp.Select) or "*" in query.named_selects:
        return None

    projections: dict[str, str] = {}
    for projection in query.expressions:
        value = projection.this if isinstance(projection, exp.Alias) else projection
        projections[projection.alias_or_name] = value.sql(dialect=dialect)
    body = query.copy()
    body.set("expressions", [exp.Star()])
    return TopSelect(list(query.named_selects), projections, body.sql(dialect=dialect))


def model_top_selects(project: Project) -> dict[str, TopSelect | None]:
    """analyse_top_select for every model, reading each against the models it reads."""
    schema = {table: dict(columns) for table, columns in project.schema.items()}
    result: dict[str, TopSelect | None] = {}
    for model in _in_dependency_order(project):
        top = analyse_top_select(model.sql, schema, project.dialect)
        result[model.name] = top
        names = top.names if top else output_columns_best_effort(model.sql, schema, project.dialect)
        schema[model.name] = dict.fromkeys(names or [], UNKNOWN_TYPE)
    return result


def output_columns_best_effort(sql: str, schema: Schema, dialect: str) -> list[str] | None:
    """The output columns of a SELECT, even when it can no longer be fully resolved.

    A model edited by a change may read a column that no longer exists, so qualifying it fails.
    Then the select list is read as written. None when the columns cannot be known: a `select *`
    that cannot be expanded.
    """
    try:
        qualified = qualify(sqlglot.parse_one(sql, read=dialect), schema=schema, dialect=dialect)
        names = list(qualified.named_selects)
    except SqlglotError:
        names = list(sqlglot.parse_one(sql, read=dialect).named_selects)
    # a star that was not expanded (its table is unknown) says nothing about the columns
    return None if "*" in names else names


def _in_dependency_order(project: Project) -> list[Model]:
    """Models sorted so that every model comes after the models it reads."""
    dependencies = {
        name: _models_read_by(model, project) for name, model in sorted(project.models.items())
    }
    try:
        order = TopologicalSorter(dependencies).static_order()
        return [project.models[name] for name in order]
    except CycleError as error:
        cycle = " -> ".join(error.args[1])
        raise CircularDependencyError(f"Models read each other in a cycle: {cycle}") from error


def _models_read_by(model: Model, project: Project) -> set[str]:
    """Names of other models that this model reads in a FROM or JOIN."""
    parsed = sqlglot.parse_one(model.sql, read=project.dialect)
    # scope.sources holds real tables as exp.Table and CTEs / subqueries as Scope, so a CTE that
    # happens to share a model's name (WITH orders AS (...)) is not counted as a read of it
    tables = {
        source.name
        for scope in traverse_scope(parsed)
        for source in scope.sources.values()
        if isinstance(source, exp.Table)
    }
    return tables & set(project.models)


def _qualified(model: Model, schema: Schema, dialect: str) -> exp.Expression:
    """Parse a model and qualify it: every column gets its table alias, `*` is expanded."""
    parsed = sqlglot.parse_one(model.sql, read=dialect)
    return qualify(parsed, schema=schema, dialect=dialect)


def _select_edges(model: Model, query: exp.Expression, schema: Schema, dialect: str) -> list[Edge]:
    """One edge per (source column, output column) pair of a model's SELECT."""
    edges: dict[Edge, None] = {}  # dict keeps order and drops duplicates

    for output_column in query.named_selects:
        root = lineage(output_column, query, schema=schema, dialect=dialect)
        target = ColumnRef(model.name, output_column)
        for leaf in _leaves(root):
            source = _source_column(leaf, dialect)
            if source is not None:
                edges[Edge(source=source, target=target, kind=SELECT)] = None

    return list(edges)


# Which clause of a SELECT a column sits in -> the edge kind it produces
_CLAUSE_KINDS: dict[type[exp.Expression], str] = {
    exp.Join: JOIN,
    exp.Where: WHERE,
    exp.Group: GROUP_BY,
    exp.Having: HAVING,
    exp.Order: ORDER_BY,
}


def _clause_edges(model: Model, query: exp.Expression) -> list[Edge]:
    """Edges from columns used in JOIN, WHERE, GROUP BY, HAVING and ORDER BY to the model.

    These columns never reach an output column, yet changing them changes the model.
    Every SELECT in the query (the main one, CTEs, subqueries) is handled in its own scope,
    so an alias like "o" can mean different tables in different SELECTs.

    A column that comes from a CTE or a subquery rather than a real table is traced back, through
    as many CTEs as it takes, to the table columns it is defined from. (dbt models are chains of
    CTEs, so most clause columns are of this kind.)

    Known limit: a CTE that is a UNION is not traced.
    """
    target = ColumnRef(model.name, None)
    edges: dict[Edge, None] = {}

    def add(sources: list[ColumnRef], kind: str) -> None:
        for source in sources:
            edges[Edge(source=source, target=target, kind=kind)] = None

    for scope in traverse_scope(query):
        select = scope.expression
        if not isinstance(select, exp.Select):
            continue
        for column in scope.columns:  # columns of this SELECT only, not of nested ones
            kind = _clause_kind(column, select)
            if kind is not None:
                add(_base_columns(column, scope), kind)

        # GROUP BY / HAVING / ORDER BY may name an output alias (ORDER BY total). Such a
        # reference has no table, so follow it back to the expression that defines it.
        projections = {p.alias_or_name: p for p in select.selects}
        for key in ("group", "having", "order"):
            clause = select.args.get(key)
            if clause is None:
                continue
            kind = _CLAUSE_KINDS[type(clause)]
            for ref in clause.find_all(exp.Column):
                projection = projections.get(ref.name)
                if ref.table or projection is None:
                    continue
                for inner in projection.find_all(exp.Column):
                    add(_base_columns(inner, scope), kind)

    return list(edges)


def _base_columns(column: exp.Column, scope: Scope) -> list[ColumnRef]:
    """The real table (or model) columns that `column` is made from.

    A column of a real table is itself. A column of a CTE or subquery is the columns used in the
    expression that defines it there, followed down the same way until real tables are reached.
    """
    source = scope.sources.get(column.table)
    if isinstance(source, exp.Table):
        return [ColumnRef(source.name, column.name)]
    if not isinstance(source, Scope) or not isinstance(source.expression, exp.Select):
        return []  # an unknown alias, or a CTE that is a UNION
    for projection in source.expression.selects:
        if projection.alias_or_name == column.name:
            return [
                base
                for inner in projection.find_all(exp.Column)
                for base in _base_columns(inner, source)
            ]
    return []


def _clause_kind(column: exp.Column, select: exp.Select) -> str | None:
    """Walk up from a column to the clause directly under its SELECT; None if not a clause."""
    node: exp.Expression = column
    while node.parent is not None and node.parent is not select:
        node = node.parent
    return _CLAUSE_KINDS.get(type(node))


def _leaves(node: Node) -> list[Node]:
    """Nodes at the bottom of a lineage tree: the real source columns."""
    return [n for n in node.walk() if not n.downstream]


def _source_column(leaf: Node, dialect: str) -> ColumnRef | None:
    """Turn a leaf like '"oi"."quantity"' (alias.column) into ColumnRef(order_items, quantity).

    The alias is resolved through leaf.source, which is the real table. Leaves that do
    not come from a table (for example a literal such as `SELECT 1 AS x`) return None.
    """
    if not isinstance(leaf.source, exp.Table):
        return None
    column = exp.to_column(leaf.name, dialect=dialect).name
    return ColumnRef(table=leaf.source.name, column=column)
