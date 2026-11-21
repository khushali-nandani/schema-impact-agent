"""Mutations of the CSV files that a dbt-duckdb project reads as sources.

Olist's files mix quoted and unquoted fields on purpose (a zip code "01037" is quoted so that its
zero is kept), so a mutation must leave every field it does not touch exactly as it was.
"""

import json
import shutil
from pathlib import Path

import pytest

from schema_impact.evals.dbt_runner import _copy_project
from schema_impact.evals.mutations import (
    SOURCE_CHANGE_TYPE,
    SOURCE_DROP_COLUMN,
    SOURCE_RENAME_COLUMN,
    Mutation,
    MutationError,
    _rewrite_csv,
    apply_mutation,
    generate_mutations,
)

FIXTURE = Path(__file__).parent / "fixtures" / "olist_dbt"

# mixed quoting, an empty field, a comma and a doubled quote inside quotes, and a CRLF inside one
MIXED = (
    b'"id","zip","note","n"\n'
    b'a1,"01037","plain",1\n'
    b'"b2","02000","has, comma",\n'
    b'c3,"03000","say ""hi""\r\nthere",3\n'
)
NO_FINAL_NEWLINE = b"x,y\n1,2"
WITH_BOM = "﻿product,english\nfoo,bar\n".encode()

SOURCES_YML = """version: 2

sources:
  - name: olist
    meta:
      external_location: "data/olist_{name}_dataset.csv"
    tables:
      - name: orders
        description: "orders"
      - name: order_items
        description: "items"
      - name: translation
        meta:
          external_location: "data/translation.csv"
"""


def write(path: Path, content: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def mutation(kind: str, **params) -> Mutation:
    return Mutation(f"{kind}:x", kind, "t.c", "d", params)


@pytest.fixture
def root(tmp_path) -> Path:
    write(tmp_path / "data" / "t.csv", MIXED)
    return tmp_path


def rename(root: Path, column: str, new_name: str = "renamed", path: str = "data/t.csv"):
    apply_mutation(
        mutation(SOURCE_RENAME_COLUMN, path=path, column=column, new_name=new_name), root
    )


def drop(root: Path, column: str, path: str = "data/t.csv"):
    apply_mutation(mutation(SOURCE_DROP_COLUMN, path=path, column=column), root)


# --- renaming a column ----------------------------------------------------------------


def test_renaming_changes_only_the_header_and_keeps_its_quoting(root):
    rename(root, "zip")
    data = (root / "data" / "t.csv").read_bytes()
    assert data.startswith(b'"id","renamed","note","n"\n')
    assert data.split(b"\n", 1)[1] == MIXED.split(b"\n", 1)[1]  # every other byte is the same


def test_renaming_an_unquoted_header_stays_unquoted(tmp_path):
    write(tmp_path / "data" / "t.csv", NO_FINAL_NEWLINE)
    rename(tmp_path, "y")
    assert (tmp_path / "data" / "t.csv").read_bytes() == b"x,renamed\n1,2"


def test_the_column_is_found_behind_a_byte_order_mark(tmp_path):
    write(tmp_path / "data" / "t.csv", WITH_BOM)
    rename(tmp_path, "product", "category")
    assert (tmp_path / "data" / "t.csv").read_bytes().decode().endswith("english\nfoo,bar\n")
    assert "category" in (tmp_path / "data" / "t.csv").read_text(encoding="utf-8-sig")


def test_renaming_a_column_that_is_not_there_fails(root):
    with pytest.raises(MutationError, match="missing"):
        rename(root, "missing")


# --- dropping a column ----------------------------------------------------------------


def test_dropping_removes_the_field_and_keeps_the_others_as_written(root):
    drop(root, "zip")
    assert (root / "data" / "t.csv").read_bytes() == (
        b'"id","note","n"\na1,"plain",1\n"b2","has, comma",\nc3,"say ""hi""\r\nthere",3\n'
    )


def test_dropping_the_last_column_works_with_an_empty_field(root):
    drop(root, "n")
    assert (root / "data" / "t.csv").read_bytes().startswith(b'"id","zip","note"\na1,')
    assert b'"b2","02000","has, comma"\n' in (root / "data" / "t.csv").read_bytes()


def test_dropping_a_column_that_is_not_there_fails(root):
    with pytest.raises(MutationError, match="missing"):
        drop(root, "missing")


def test_a_file_that_cannot_be_split_is_refused_not_guessed(tmp_path):
    write(tmp_path / "data" / "t.csv", b'a,b\n1,x"y\n')  # a quote in the middle of a field
    with pytest.raises(MutationError, match="split"):
        drop(tmp_path, "a")


# --- rewriting without a change is exact ---------------------------------------------------


@pytest.mark.parametrize("content", [MIXED, NO_FINAL_NEWLINE, WITH_BOM, b"a,b,\n1,2,\n"])
def test_a_rewrite_that_drops_nothing_gives_the_same_bytes(tmp_path, content):
    path = write(tmp_path / "t.csv", content)
    _rewrite_csv(path, set())
    assert path.read_bytes() == content


# --- the original file is never touched --------------------------------------------------------


def test_the_original_is_left_alone_when_the_copy_is_edited(tmp_path):
    source = tmp_path / "source"
    write(source / "dbt_project.yml", b"name: p\n")
    write(source / "data" / "t.csv", MIXED)
    copy = tmp_path / "copy"
    _copy_project(source, copy)
    rename(copy, "zip")
    drop(copy, "note")
    assert (source / "data" / "t.csv").read_bytes() == MIXED


# --- changing a type ---------------------------------------------------------------------------


@pytest.fixture
def project(tmp_path) -> Path:
    write(tmp_path / "models" / "_sources.yml", SOURCES_YML.encode())
    return tmp_path


def retype(root: Path, table: str, column: str = "price", new_type: str = "VARCHAR"):
    apply_mutation(
        mutation(
            SOURCE_CHANGE_TYPE,
            yaml="models/_sources.yml",
            source="olist",
            table=table,
            path=f"data/olist_{table}_dataset.csv",
            column=column,
            new_type=new_type,
        ),
        root,
    )


def test_a_type_change_gives_the_table_its_own_read_csv_location(project):
    # no braces: dbt renders the location as Jinja and then formats it with str.format_map, and
    # both would take {'price': ...} for something to fill in. struct_pack has none.
    retype(project, "orders")
    text = (project / "models" / "_sources.yml").read_text()
    assert (
        "      - name: orders\n"
        "        meta:\n"
        "          external_location: \"read_csv('data/olist_orders_dataset.csv', "
        "types=struct_pack(price := 'VARCHAR'))\"\n"
        '        description: "orders"\n'
    ) in text


def test_only_the_named_table_is_edited(project):
    retype(project, "orders")
    text = (project / "models" / "_sources.yml").read_text()
    assert text.count("read_csv") == 1
    # the neighbour with a similar name and the table with its own location are unchanged
    assert '      - name: order_items\n        description: "items"\n' in text
    assert '        meta:\n          external_location: "data/translation.csv"\n' in text


def test_a_column_name_that_is_not_a_plain_identifier_is_refused(project):
    with pytest.raises(MutationError, match="identifier"):
        retype(project, "orders", column="has space")


def test_a_table_that_already_has_its_own_location_is_refused(project):
    with pytest.raises(MutationError, match="meta"):
        retype(project, "translation")


def test_a_table_that_is_not_in_the_file_is_refused(project):
    with pytest.raises(MutationError, match="nothing"):
        retype(project, "nothing")


# --- generating them for a project ---------------------------------------------------------------


@pytest.fixture
def olist(tmp_path) -> Path:
    pytest.importorskip("duckdb")
    root = tmp_path / "olist"
    shutil.copytree(FIXTURE, root)
    orders = (
        "order_id,customer_id,order_status,order_purchase_timestamp,order_approved_at,"
        "order_delivered_carrier_date,order_delivered_customer_date,"
        "order_estimated_delivery_date\n"
        "o1,c1,delivered,2020-01-01 10:00:00,2020-01-01 11:00:00,"
        "2020-01-02 10:00:00,2020-01-05 10:00:00,2020-01-07 00:00:00\n"
    )
    items = (
        "order_id,order_item_id,product_id,seller_id,shipping_limit_date,price,freight_value\n"
        "o1,1,p1,s1,2020-01-03 10:00:00,12.5,2.0\n"
    )
    for model in (
        "models/staging/stg_olist__orders.sql",
        "models/staging/stg_olist__order_items.sql",
        "models/intermediate/int_order_items_aggregated.sql",
    ):  # only the sources are mutated here; the models just have to exist
        write(root / model, b"select 1 as x\n")
    write(root / "data" / "olist_orders_dataset.csv", orders.encode())
    write(root / "data" / "olist_order_items_dataset.csv", items.encode())
    return root


def by_kind(mutations: list[Mutation]) -> dict[str, list[Mutation]]:
    result: dict[str, list[Mutation]] = {}
    for m in mutations:
        result.setdefault(m.kind, []).append(m)
    return result


def test_every_source_column_gets_a_rename_and_a_drop(olist):
    kinds = by_kind(generate_mutations(olist))
    assert len(kinds[SOURCE_RENAME_COLUMN]) == 8 + 7
    assert len(kinds[SOURCE_DROP_COLUMN]) == 8 + 7


def test_only_columns_that_are_not_text_get_a_type_change(olist):
    changes = by_kind(generate_mutations(olist))[SOURCE_CHANGE_TYPE]
    targets = {m.target for m in changes}
    assert "olist__order_items.price" in targets
    assert "olist__order_items.order_item_id" in targets
    assert "olist__orders.order_status" not in targets  # already text
    assert len(changes) == 5 + 4  # timestamps of orders; item number, timestamp, two amounts


def test_a_mutation_is_named_after_the_source_and_points_at_its_file(olist):
    rename_price = next(
        m
        for m in generate_mutations(olist)
        if m.id.endswith("olist__order_items.price") and m.kind == SOURCE_RENAME_COLUMN
    )
    assert rename_price.target == "olist__order_items.price"
    assert rename_price.params == {
        "path": "data/olist_order_items_dataset.csv",
        "column": "price",
        "new_name": "price_v2",
    }


def test_the_ids_are_unique_and_the_order_is_the_same_every_time(olist):
    first = [m.id for m in generate_mutations(olist)]
    assert len(first) == len(set(first))
    assert first == [m.id for m in generate_mutations(olist)]


def test_a_generated_mutation_survives_a_round_trip_through_json(olist):
    for m in generate_mutations(olist):
        assert Mutation.from_dict(json.loads(json.dumps(m.to_dict()))) == m
