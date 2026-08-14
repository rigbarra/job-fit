import pytest
from sqlmodel import SQLModel, create_engine

import src.database.repository as repo


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    """
    Fixture que se ejecuta automáticamente para todas las pruebas.
    Reemplaza el motor de la base de datos de producción por uno en memoria de SQLite
    para garantizar pruebas aisladas y rápidas sin tocar la base de datos real.
    """
    # Crear motor SQLite en memoria
    test_engine = create_engine("sqlite://", connect_args={"check_same_thread": False})

    # Reemplazar el motor del repositorio globalmente durante las pruebas
    monkeypatch.setattr(repo, "engine", test_engine)

    # Crear las tablas en la base de datos temporal
    SQLModel.metadata.create_all(test_engine)

    yield

    # Limpieza
    SQLModel.metadata.drop_all(test_engine)
