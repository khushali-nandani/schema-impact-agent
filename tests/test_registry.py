from pathlib import Path

import pytest

from schema_impact.core.registry import load_project


def test_loads_all_models(toy_project_path):
    project = load_project(toy_project_path)
    assert set(project.models) == {"customer_totals", "product_sales"}


def test_model_keeps_its_sql(toy_project_path):
    project = load_project(toy_project_path)
    model = project.models["customer_totals"]
    assert model.name == "customer_totals"
    assert model.sql.lstrip().upper().startswith("SELECT")
    assert "GROUP BY" in model.sql.upper()


def test_loads_all_tables(toy_project_path):
    project = load_project(toy_project_path)
    assert set(project.schema) == {"customers", "products", "orders", "order_items"}


def test_table_columns_keep_order(toy_project_path):
    project = load_project(toy_project_path)
    assert list(project.schema["orders"]) == ["id", "customer_id", "status", "amount"]


def test_column_types_are_loaded(toy_project_path):
    project = load_project(toy_project_path)
    assert project.schema["orders"]["amount"] == "DECIMAL(10, 2)"
    assert project.schema["orders"]["customer_id"] == "INT"


def test_missing_folder_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_project(tmp_path / "does_not_exist")
