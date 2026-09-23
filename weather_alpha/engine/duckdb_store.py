from __future__ import annotations

from typing import Any, Iterator


class DuckDBStore:
    """Lazy historical query layer: Parquet stays on disk and is scanned out-of-core."""

    def __init__(self, database: str = ":memory:"):
        try:
            import duckdb
        except ImportError as exc:
            raise RuntimeError("Install duckdb to query historical weather data") from exc
        self.connection = duckdb.connect(database)

    def query(self, sql: str, parameters: list[Any] | None = None):
        return self.connection.execute(sql, parameters or []).fetchall()

    def iter_query(self, sql: str, parameters: list[Any] | None = None, batch_size: int = 2048) -> Iterator[tuple[Any, ...]]:
        cursor = self.connection.execute(sql, parameters or [])
        while rows := cursor.fetchmany(batch_size):
            yield from rows

    def describe_parquet(self, glob: str):
        return self.query("DESCRIBE SELECT * FROM read_parquet(?)", [glob])

    def parquet_query(self, glob: str, columns: str = "*", where: str = "TRUE", parameters: list[Any] | None = None):
        return self.query(f"SELECT {columns} FROM read_parquet(?) WHERE {where}", [glob, *(parameters or [])])
