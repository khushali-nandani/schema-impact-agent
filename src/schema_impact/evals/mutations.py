"""Mutations of a dbt project, used to build the benchmark.

A `Mutation` is a small JSON-serialisable description of one change (rename a seed column, drop a
model column, edit a model's logic, ...). `generate_mutations` lists every mutation that makes
sense for a project; `apply_mutation` applies one to a copy of the project's files. What a
mutation *means* (breaking, silent or safe) is not decided here: dbt is run on the mutated
project to find out.

Kinds:
- seed_rename_column / seed_drop_column  edit a seed's CSV
- seed_change_type                       write a seed properties file with a new column type
- source_rename_column / source_drop_column  edit a CSV file that a dbt-duckdb source reads
- source_change_type                     read one column of such a file as varchar (read_csv types)
- model_rename_column / model_drop_column  rewrite the model's final `select * from <cte>`
- model_logic                            a hand-written text replacement (see `Recipe`)

The column mutations on a model need its final statement to be `select * from <something>`, as in
the jaffle_shop models. A model that ends differently gets no column mutations.

Every edit checks that the text it expects is really there, and fails with `MutationError`
otherwise, so a mutation can never silently do nothing.
"""

import csv
import json
import os
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEED_RENAME_COLUMN = "seed_rename_column"
SEED_DROP_COLUMN = "seed_drop_column"
SEED_CHANGE_TYPE = "seed_change_type"
SOURCE_RENAME_COLUMN = "source_rename_column"
SOURCE_DROP_COLUMN = "source_drop_column"
SOURCE_CHANGE_TYPE = "source_change_type"
MODEL_RENAME_COLUMN = "model_rename_column"
MODEL_DROP_COLUMN = "model_drop_column"
MODEL_LOGIC = "model_logic"

SEED_TYPES_FILE = "seeds/_mutation_types.yml"
_TEXT_TYPES = {"VARCHAR", "TEXT"}
_FINAL_STAR_SELECT = re.compile(r"(?is)\bselect\s+\*\s+from\s+(?P<source>\w+)\s*;?\s*\Z")


class MutationError(Exception):
    """A mutation could not be generated or applied."""


@dataclass(frozen=True)
class Mutation:
    id: str
    kind: str
    target: str  # what is changed, e.g. "raw_orders.user_id" or "stg_orders"
    description: str
    params: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "description": self.description,
            "params": self.params,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Mutation":
        return cls(data["id"], data["kind"], data["target"], data["description"], data["params"])


@dataclass(frozen=True)
class Recipe:
    """A hand-written edit of one model's source: replace `old` with `new`.

    `count` is how many times `old` must occur (1 by default); None means "at least once, and
    replace every occurrence".
    """

    id: str
    model: str
    description: str
    old: str
    new: str
    count: int | None = 1


# --- generating -------------------------------------------------------------


def generate_mutations(project_dir: str | Path, recipes: Sequence[Recipe] = ()) -> list[Mutation]:
    """Every mutation for a dbt project that has been run (target/ artifacts must exist)."""
    root = Path(project_dir)
    seeds, models = _read_artifacts(root)
    mutations: list[Mutation] = []

    for name, seed in sorted(seeds.items()):
        for column, _ in seed["columns"]:
            mutations.append(_seed_rename(name, seed["path"], column))
    for name, seed in sorted(seeds.items()):
        if len(seed["columns"]) > 1:
            for column, _ in seed["columns"]:
                mutations.append(_seed_drop(name, seed["path"], column))
    for name, seed in sorted(seeds.items()):
        for column, column_type in seed["columns"]:
            if column_type.upper() not in _TEXT_TYPES:
                mutations.append(_seed_change_type(name, column))

    sources = _read_sources(root)
    for name, source in sources.items():
        for column, _ in source["columns"]:
            mutations.append(_source_rename(name, source["path"], column))
    for name, source in sources.items():
        if len(source["columns"]) > 1:
            for column, _ in source["columns"]:
                mutations.append(_source_drop(name, source["path"], column))
    for name, source in sources.items():
        for column, column_type in source["columns"]:
            if column_type.upper() not in _TEXT_TYPES:
                mutations.append(_source_change_type(name, source, column))

    shaped = {
        name: model
        for name, model in sorted(models.items())
        if _FINAL_STAR_SELECT.search((root / model["path"]).read_text())
    }
    for name, model in shaped.items():
        for column in model["columns"]:
            mutations.append(_model_rename(name, model, column))
    for name, model in shaped.items():
        if len(model["columns"]) > 1:
            for column in model["columns"]:
                mutations.append(_model_drop(name, model, column))

    for recipe in recipes:
        mutations.append(_model_logic(root, models, recipe))

    return mutations


def _read_artifacts(root: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Seeds and models of the project with their file paths and columns, from target/."""
    manifest = json.loads((root / "target" / "manifest.json").read_text())
    catalog = json.loads((root / "target" / "catalog.json").read_text())

    def columns_of(unique_id: str) -> list[tuple[str, str]]:
        cataloged = catalog["nodes"].get(unique_id, {}).get("columns", {})
        ordered = sorted(cataloged.values(), key=lambda column: column["index"])
        return [(column["name"], column["type"]) for column in ordered]

    seeds: dict[str, dict] = {}
    models: dict[str, dict] = {}
    for unique_id, node in manifest["nodes"].items():
        columns = columns_of(unique_id)
        if node["resource_type"] == "seed":
            seeds[node["name"]] = {"path": node["original_file_path"], "columns": columns}
        elif node["resource_type"] == "model":
            models[node["name"]] = {
                "path": node["original_file_path"],
                "columns": [name for name, _ in columns],
            }
    return seeds, models


def _read_sources(root: Path) -> dict[str, dict]:
    """The sources that are CSV files read by path: name -> where the file is and its columns.

    The name is the one the adapter gives the source (`olist__orders`). Columns and types are
    read from the file, since dbt does not catalog such a source.
    """
    manifest = json.loads((root / "target" / "manifest.json").read_text())
    files: dict[str, dict] = {}
    for node in manifest.get("sources", {}).values():
        relation = node.get("relation_name") or ""
        if len(relation) > 2 and relation[0] == relation[-1] == "'":
            files[f"{node['source_name']}__{node['name']}"] = {
                "path": relation[1:-1],
                "yaml": node["original_file_path"],
                "source": node["source_name"],
                "table": node["name"],
            }
    if not files:
        return {}
    from schema_impact.adapters.dbt_manifest import load_dbt_project  # needs duckdb

    schema = load_dbt_project(root).schema
    for name, source in files.items():
        source["columns"] = list(schema[name].items())
    return dict(sorted(files.items()))


def _source_rename(source: str, path: str, column: str) -> Mutation:
    new_name = f"{column}_v2"
    return Mutation(
        id=f"{SOURCE_RENAME_COLUMN}:{source}.{column}",
        kind=SOURCE_RENAME_COLUMN,
        target=f"{source}.{column}",
        description=f"Rename column {column} of source {source} to {new_name}",
        params={"path": path, "column": column, "new_name": new_name},
    )


def _source_drop(source: str, path: str, column: str) -> Mutation:
    return Mutation(
        id=f"{SOURCE_DROP_COLUMN}:{source}.{column}",
        kind=SOURCE_DROP_COLUMN,
        target=f"{source}.{column}",
        description=f"Drop column {column} of source {source}",
        params={"path": path, "column": column},
    )


def _source_change_type(name: str, source: dict, column: str) -> Mutation:
    return Mutation(
        id=f"{SOURCE_CHANGE_TYPE}:{name}.{column}",
        kind=SOURCE_CHANGE_TYPE,
        target=f"{name}.{column}",
        description=f"Read column {column} of source {name} as varchar",
        params={
            "yaml": source["yaml"],
            "source": source["source"],
            "table": source["table"],
            "path": source["path"],
            "column": column,
            "new_type": "VARCHAR",
        },
    )


def _seed_rename(seed: str, path: str, column: str) -> Mutation:
    new_name = f"{column}_v2"
    return Mutation(
        id=f"{SEED_RENAME_COLUMN}:{seed}.{column}",
        kind=SEED_RENAME_COLUMN,
        target=f"{seed}.{column}",
        description=f"Rename seed column {seed}.{column} to {new_name}",
        params={"path": path, "column": column, "new_name": new_name},
    )


def _seed_drop(seed: str, path: str, column: str) -> Mutation:
    return Mutation(
        id=f"{SEED_DROP_COLUMN}:{seed}.{column}",
        kind=SEED_DROP_COLUMN,
        target=f"{seed}.{column}",
        description=f"Drop seed column {seed}.{column}",
        params={"path": path, "column": column},
    )


def _seed_change_type(seed: str, column: str) -> Mutation:
    return Mutation(
        id=f"{SEED_CHANGE_TYPE}:{seed}.{column}",
        kind=SEED_CHANGE_TYPE,
        target=f"{seed}.{column}",
        description=f"Load seed column {seed}.{column} as varchar",
        params={"seed": seed, "column": column, "new_type": "varchar"},
    )


def _model_rename(model: str, info: dict, column: str) -> Mutation:
    new_name = f"{column}_v2"
    return Mutation(
        id=f"{MODEL_RENAME_COLUMN}:{model}.{column}",
        kind=MODEL_RENAME_COLUMN,
        target=f"{model}.{column}",
        description=f"Rename output column {model}.{column} to {new_name}",
        params={
            "path": info["path"],
            "column": column,
            "new_name": new_name,
            "columns": info["columns"],
        },
    )


def _model_drop(model: str, info: dict, column: str) -> Mutation:
    return Mutation(
        id=f"{MODEL_DROP_COLUMN}:{model}.{column}",
        kind=MODEL_DROP_COLUMN,
        target=f"{model}.{column}",
        description=f"Drop output column {model}.{column}",
        params={"path": info["path"], "column": column, "columns": info["columns"]},
    )


def _model_logic(root: Path, models: dict[str, dict], recipe: Recipe) -> Mutation:
    info = models.get(recipe.model)
    if info is None:
        raise MutationError(f"Recipe {recipe.id}: model {recipe.model} not found in the project")
    mutation = Mutation(
        id=f"{MODEL_LOGIC}:{recipe.id}",
        kind=MODEL_LOGIC,
        target=recipe.model,
        description=recipe.description,
        params={"path": info["path"], "old": recipe.old, "new": recipe.new, "count": recipe.count},
    )
    try:
        _check_occurrences(
            (root / info["path"]).read_text(), recipe.old, recipe.count, info["path"]
        )
    except MutationError as error:
        raise MutationError(f"Recipe {recipe.id} does not apply: {error}") from error
    return mutation


# --- applying ---------------------------------------------------------------


def apply_mutation(mutation: Mutation, project_dir: str | Path) -> None:
    """Apply a mutation to the project files in `project_dir`. Edits the files in place."""
    handler = _APPLIERS.get(mutation.kind)
    if handler is None:
        raise MutationError(f"Unknown mutation kind: {mutation.kind}")
    handler(mutation.params, Path(project_dir))


def _read_csv(path: Path) -> list[list[str]]:
    with path.open(newline="") as handle:
        return list(csv.reader(handle))


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    """Write the file by replacing it. If `path` is a link to a shared file (see dbt_runner.py),
    the link is replaced and the file it points to is left as it was."""
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="") as handle:
        csv.writer(handle, lineterminator="\n").writerows(rows)
    os.replace(temporary, path)


def _column_index(header: list[str], column: str, path: Path) -> int:
    if column not in header:
        raise MutationError(f"Column {column} not found in the header of {path}")
    return header.index(column)


def _apply_seed_rename(params: dict, root: Path) -> None:
    path = root / params["path"]
    rows = _read_csv(path)
    rows[0][_column_index(rows[0], params["column"], path)] = params["new_name"]
    _write_csv(path, rows)


def _apply_seed_drop(params: dict, root: Path) -> None:
    path = root / params["path"]
    rows = _read_csv(path)
    index = _column_index(rows[0], params["column"], path)
    _write_csv(path, [row[:index] + row[index + 1 :] for row in rows])


def _apply_seed_change_type(params: dict, root: Path) -> None:
    path = root / SEED_TYPES_FILE
    if path.exists():
        raise MutationError(f"{SEED_TYPES_FILE} already exists")
    path.write_text(
        "version: 2\n"
        "seeds:\n"
        f"  - name: {params['seed']}\n"
        "    config:\n"
        "      column_types:\n"
        f"        {params['column']}: {params['new_type']}\n"
    )


# One field of a CSV file as written (quoted or not), then what follows it: a comma, the end of
# the row or the end of the file. Nothing is unquoted or re-quoted, so what is not changed stays
# byte for byte as it was.
_CSV_FIELD = re.compile(r'("[^"]*(?:""[^"]*)*"|[^,"\r\n]*)(,|\r\n|\n|\r|$)')


def _split_csv(text: str, where: Path) -> Iterator[tuple[list[str], str]]:
    """Rows of a CSV text as (fields exactly as written, the line ending that ends the row)."""
    position, fields = 0, []
    while position < len(text):
        match = _CSV_FIELD.match(text, position)
        if match is None or match.end() == position:
            raise MutationError(f"Cannot split {where} into fields at character {position}")
        fields.append(match[1])
        position = match.end()
        if match[2] != ",":
            yield fields, match[2]
            fields = []
    if fields:  # the text ends with a comma: one more, empty, field
        yield [*fields, ""], ""


def _field_value(raw: str) -> str:
    """The value of a field as written: without its quotes and a leading byte order mark."""
    raw = raw.lstrip("\ufeff")
    return raw[1:-1].replace('""', '"') if raw.startswith('"') else raw


def _read_text(path: Path) -> str:
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _replace_text(path: Path, text: str) -> None:
    """Write `text` as the new content of `path` by replacing the file (see `_write_csv`)."""
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    os.replace(temporary, path)


def _header_of(text: str, path: Path) -> tuple[list[str], str]:
    header = next(_split_csv(text, path), None)
    if header is None:
        raise MutationError(f"{path} is empty")
    return header


def _apply_source_rename(params: dict, root: Path) -> None:
    path = root / params["path"]
    text = _read_text(path)
    fields, ending = _header_of(text, path)
    index = _column_index([_field_value(f) for f in fields], params["column"], path)
    header_length = len(",".join(fields)) + len(ending)
    raw = fields[index]
    bom = "\ufeff" if raw.startswith("\ufeff") else ""
    new_name = params["new_name"]
    fields[index] = bom + (f'"{new_name}"' if raw[len(bom) :].startswith('"') else new_name)
    _replace_text(path, ",".join(fields) + ending + text[header_length:])


def _rewrite_csv(path: Path, drop: Iterable[str]) -> None:
    """Rewrite a CSV file without the columns named in `drop`, every other field as written.

    With nothing to drop the file comes out byte for byte the same.
    """
    names_to_drop = set(drop)
    text = _read_text(path)
    rows = _split_csv(text, path)
    header = next(rows, None)
    if header is None:
        raise MutationError(f"{path} is empty")
    names = [_field_value(f) for f in header[0]]
    for name in names_to_drop - set(names):
        raise MutationError(f"Column {name} not found in the header of {path}")
    gone = {i for i, name in enumerate(names) if name in names_to_drop}

    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        for fields, ending in [header, *rows]:
            handle.write(",".join(f for i, f in enumerate(fields) if i not in gone) + ending)
    os.replace(temporary, path)


def _apply_source_drop(params: dict, root: Path) -> None:
    _rewrite_csv(root / params["path"], {params["column"]})


def _apply_source_change_type(params: dict, root: Path) -> None:
    """Give the table its own `external_location`: a read_csv call with the column's type."""
    path = root / params["yaml"]
    text = path.read_text()
    table = params["table"]
    heading = re.compile(rf"(?m)^(?P<indent> *)- name: {re.escape(table)}[ \t]*\n")
    found = list(heading.finditer(text))
    if len(found) != 1:
        raise MutationError(
            f"Expected one table named {table} in {params['yaml']}, found {len(found)}"
            + (" (nothing to edit)" if not found else "")
        )
    indent = found[0]["indent"]
    for line in text[found[0].end() :].splitlines():
        if line.strip() and len(line) - len(line.lstrip()) < len(indent) + 2:
            break  # the next table or the end of this one
        if line.strip().startswith("meta:"):
            raise MutationError(f"Table {table} in {params['yaml']} already has its own meta")
    column = params["column"]
    if not re.fullmatch(r"\w+", column):
        raise MutationError(f"Column {column!r} is not a plain identifier")
    # no braces: dbt renders the location as Jinja and then formats it with str.format_map, and
    # both would take {'price': ...} for something to fill in
    types = f"struct_pack({column} := '{params['new_type']}')"
    call = f"read_csv('{params['path']}', types={types})"
    added = f'{indent}  meta:\n{indent}    external_location: "{call}"\n'
    path.write_text(text[: found[0].end()] + added + text[found[0].end() :])


def _final_select(path: Path) -> tuple[str, re.Match[str]]:
    text = path.read_text()
    match = _FINAL_STAR_SELECT.search(text)
    if match is None:
        raise MutationError(f"{path} does not end with `select * from <name>`")
    return text, match


def _rewrite_final_select(params: dict, root: Path, columns: list[str]) -> None:
    path = root / params["path"]
    text, match = _final_select(path)
    path.write_text(f"{text[: match.start()]}select {', '.join(columns)} from {match['source']}\n")


def _apply_model_rename(params: dict, root: Path) -> None:
    _column_index(params["columns"], params["column"], root / params["path"])
    columns = [
        f"{c} as {params['new_name']}" if c == params["column"] else c for c in params["columns"]
    ]
    _rewrite_final_select(params, root, columns)


def _apply_model_drop(params: dict, root: Path) -> None:
    _column_index(params["columns"], params["column"], root / params["path"])
    _rewrite_final_select(params, root, [c for c in params["columns"] if c != params["column"]])


def _check_occurrences(text: str, old: str, count: int | None, where: str) -> None:
    found = text.count(old)
    if found == 0:
        raise MutationError(f"Text {old!r} not found in {where}")
    if count is not None and found != count:
        raise MutationError(
            f"Text {old!r} in {where}: expected {count} occurrence(s), found {found}"
        )


def _apply_model_logic(params: dict, root: Path) -> None:
    path = root / params["path"]
    text = path.read_text()
    _check_occurrences(text, params["old"], params["count"], params["path"])
    path.write_text(text.replace(params["old"], params["new"]))


_APPLIERS: dict[str, Callable[[dict, Path], None]] = {
    SEED_RENAME_COLUMN: _apply_seed_rename,
    SEED_DROP_COLUMN: _apply_seed_drop,
    SEED_CHANGE_TYPE: _apply_seed_change_type,
    SOURCE_RENAME_COLUMN: _apply_source_rename,
    SOURCE_DROP_COLUMN: _apply_source_drop,
    SOURCE_CHANGE_TYPE: _apply_source_change_type,
    MODEL_RENAME_COLUMN: _apply_model_rename,
    MODEL_DROP_COLUMN: _apply_model_drop,
    MODEL_LOGIC: _apply_model_logic,
}
