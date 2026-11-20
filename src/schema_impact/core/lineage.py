"""Column-level lineage: which source columns a model depends on, and how.

Edge kinds:
- select:   the source column feeds one output column of the model
- join:     the source column is used in a JOIN condition (affects the whole model)
- where:    the source column is used in a WHERE filter (affects the whole model)
- group_by: the source column is used in GROUP BY (affects the whole model)
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.lineage import Node, lineage
from sqlglot.optimizer.qualify import qualify

from schema_impact.core.registry import Model, Project

SELECT = "select"
JOIN = "join"
WHERE = "where"
GROUP_BY = "group_by"


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


def build_lineage(project: Project) -> list[Edge]:
    """Return every lineage edge for every model in the project."""
    edges: list[Edge] = []
    for model in project.models.values():
        edges.extend(_select_edges(model, project))
        edges.extend(_clause_edges(model, project))
    return edges


def _qualified(model: Model, project: Project) -> exp.Expression:
    """Parse a model and qualify it: every column gets its table alias, `*` is expanded."""
    parsed = sqlglot.parse_one(model.sql, read=project.dialect)
    return qualify(parsed, schema=project.schema, dialect=project.dialect)


def _select_edges(model: Model, project: Project) -> list[Edge]:
    """One edge per (source column, output column) pair of a model's SELECT."""
    query = _qualified(model, project)
    edges: dict[Edge, None] = {}  # dict keeps order and drops duplicates

    for output_column in query.named_selects:
        root = lineage(output_column, query, schema=project.schema, dialect=project.dialect)
        target = ColumnRef(model.name, output_column)
        for leaf in _leaves(root):
            source = _source_column(leaf, project.dialect)
            if source is not None:
                edges[Edge(source=source, target=target, kind=SELECT)] = None

    return list(edges)


def _clause_edges(model: Model, project: Project) -> list[Edge]:
    """Edges from columns used in JOIN, WHERE and GROUP BY to the whole model.

    These columns never reach an output column, yet changing them changes the model.
    """
    query = _qualified(model, project)
    real_tables = {t.alias_or_name: t.name for t in query.find_all(exp.Table)}
    target = ColumnRef(model.name, None)

    clauses: list[tuple[str, exp.Expression | None]] = [
        (JOIN, join.args.get("on")) for join in query.args.get("joins", [])
    ]
    clauses.append((WHERE, query.args.get("where")))
    clauses.append((GROUP_BY, query.args.get("group")))

    edges: dict[Edge, None] = {}
    for kind, clause in clauses:
        if clause is None:
            continue
        for column in clause.find_all(exp.Column):
            table = real_tables.get(column.table)
            if table is not None:
                source = ColumnRef(table, column.name)
                edges[Edge(source=source, target=target, kind=kind)] = None

    return list(edges)


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
