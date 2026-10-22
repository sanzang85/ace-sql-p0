"""Bounded MySQL read-only execution shared by lifecycle SQL and value probes."""
from __future__ import annotations

import os


def run_probe(sql, params=()):
    from .config import get_db_config
    import pymysql
    config = dict(get_db_config())
    timeout = max(1, min(int(os.getenv("NL2SQL_READ_TIMEOUT", "15")), 120))
    config.update(connect_timeout=timeout, read_timeout=timeout, write_timeout=timeout)
    connection = pymysql.connect(**config, cursorclass=pymysql.cursors.SSDictCursor)
    max_rows = max(1, min(int(os.getenv("NL2SQL_MAX_RESULT_ROWS", "5000")), 100000))
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME = %s", (timeout * 1000,))
            cursor.execute("START TRANSACTION READ ONLY")
            cursor.execute(sql, tuple(params) if params else None)
            rows = list(cursor.fetchmany(max_rows + 1))
            if len(rows) > max_rows:
                raise ValueError(f"Query exceeded the configured result limit ({max_rows}); narrow the query")
            return rows
    finally:
        connection.close()
