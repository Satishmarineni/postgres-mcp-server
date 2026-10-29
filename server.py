"""
server.py
MCP server that lets an AI client explore and query the mcp_demo PostgreSQL database.
Read-only by design: read-only DB user + read-only transaction + SQL validation.

Tools:
  get_schema      - full schema in one call (call this first)
  list_tables     - tables with row counts
  describe_table  - details for one table
  run_query       - run a single read-only SELECT
"""

import os
import re
import sys
import json
import logging

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

# Load .env from this file's folder (MCP clients may start the server from a different folder)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# IMPORTANT: stdio servers must never print to stdout - it breaks the protocol. Log to stderr.
logging.basicConfig(stream=sys.stderr, level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("postgres-mcp")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": os.getenv("DB_PORT", "5432"),
    "dbname": os.getenv("DB_NAME", "mcp_demo"),
    "user": os.getenv("DB_USER", "mcp_readonly"),
    "password": os.getenv("DB_PASSWORD", ""),
}
MAX_ROWS = 200
STATEMENT_TIMEOUT_MS = 5000

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE|COPY|"
    r"CALL|DO|EXECUTE|PREPARE|VACUUM|ANALYZE|REINDEX|CLUSTER|COMMENT|LOCK|SET|RESET|"
    r"LISTEN|NOTIFY|REFRESH|SECURITY|INTO)\b",
    re.IGNORECASE,
)

mcp = MCPServer("postgres-mcp")


# ---------------- Helpers ----------------
def get_connection():
    """Open a read-only connection with a statement timeout."""
    conn = psycopg2.connect(**DB_CONFIG, options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}")
    conn.set_session(readonly=True, autocommit=False)
    return conn


def to_json(data) -> str:
    # default=str handles Decimal, date, datetime
    return json.dumps(data, default=str, indent=2)


def validate_query(query: str) -> str:
    """Allow only a single SELECT/WITH statement. Raises ValueError otherwise."""
    q = query.strip().rstrip(";").strip()
    if not q:
        raise ValueError("Query is empty.")

    # Ignore text inside string literals when checking keywords, e.g. WHERE reason = 'Do this'
    no_strings = re.sub(r"'(?:[^']|'')*'", "''", q)
    # Strip comments so keywords can't hide in them
    no_strings = re.sub(r"--[^\n]*|/\*.*?\*/", " ", no_strings, flags=re.DOTALL)

    if ";" in no_strings:
        raise ValueError("Only one SQL statement is allowed.")
    if not re.match(r"^\s*(SELECT|WITH)\b", no_strings, re.IGNORECASE):
        raise ValueError("Only SELECT (or WITH ... SELECT) queries are allowed.")
    match = FORBIDDEN.search(no_strings)
    if match:
        raise ValueError(f"Keyword '{match.group(0).upper()}' is not allowed. This server is read-only.")
    return q


def get_table_names(cur) -> list[str]:
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
    """)
    return [row["table_name"] for row in cur.fetchall()]


# ---------------- Tools ----------------
@mcp.tool()
def get_schema() -> str:
    """Return the FULL database schema in one call: every table with its columns, data types,
    primary keys (PK), foreign keys (->) and allowed values (CHECK constraints).
    Call this FIRST, before writing any SQL. Use only the exact names it returns."""
    log.info("get_schema called")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # Columns and types
            cur.execute("""
                SELECT table_name, column_name, data_type
                FROM information_schema.columns
                WHERE table_schema = 'public'
                ORDER BY table_name, ordinal_position
            """)
            columns = cur.fetchall()

            # Primary keys (pg_catalog, because information_schema hides constraints from SELECT-only users)
            cur.execute("""
                SELECT cl.relname AS table_name, a.attname AS column_name
                FROM pg_index i
                JOIN pg_class cl ON cl.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = cl.relnamespace
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indisprimary AND n.nspname = 'public'
            """)
            pks = {(r["table_name"], r["column_name"]) for r in cur.fetchall()}

            # Foreign keys
            cur.execute("""
                SELECT cl.relname AS table_name, a.attname AS column_name,
                       cf.relname AS ref_table, af.attname AS ref_column
                FROM pg_constraint c
                JOIN pg_class cl ON cl.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = cl.relnamespace
                JOIN pg_class cf ON cf.oid = c.confrelid
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = c.confkey[1]
                WHERE c.contype = 'f' AND n.nspname = 'public'
            """)
            fks = {(r["table_name"], r["column_name"]): f'{r["ref_table"]}.{r["ref_column"]}'
                   for r in cur.fetchall()}

            # CHECK constraints (e.g. allowed status values)
            cur.execute("""
                SELECT cl.relname AS table_name, pg_get_constraintdef(c.oid) AS definition
                FROM pg_constraint c
                JOIN pg_class cl ON cl.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = cl.relnamespace
                WHERE c.contype = 'c' AND n.nspname = 'public'
                ORDER BY cl.relname
            """)
            checks = cur.fetchall()

        # Build a compact, model-friendly summary
        tables = {}
        for c in columns:
            key = (c["table_name"], c["column_name"])
            desc = f'{c["column_name"]} {c["data_type"]}'
            if key in pks:
                desc += " PK"
            if key in fks:
                desc += f" -> {fks[key]}"
            tables.setdefault(c["table_name"], []).append(desc)

        lines = [f"{table}({', '.join(cols)})" for table, cols in tables.items()]
        if checks:
            lines.append("\nAllowed values:")
            lines += [f"  {r['table_name']}: {r['definition']}" for r in checks]
        return "\n".join(lines)
    finally:
        conn.close()


@mcp.tool()
def list_tables() -> str:
    """List all tables in the database with their row counts."""
    log.info("list_tables called")
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            result = []
            for name in get_table_names(cur):
                cur.execute(sql.SQL("SELECT COUNT(*) AS n FROM {}").format(sql.Identifier(name)))
                result.append({"table": name, "row_count": cur.fetchone()["n"]})
        return to_json(result)
    finally:
        conn.close()


@mcp.tool()
def describe_table(table_name: str) -> str:
    """Describe one table's columns (name, type, nullable), primary key, and foreign keys.
    Prefer get_schema to see all tables at once."""
    log.info("describe_table called: %s", table_name)
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            tables = get_table_names(cur)
            if table_name not in tables:
                return f"Table '{table_name}' not found. Available tables: {', '.join(tables)}"

            cur.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                ORDER BY ordinal_position
            """, (table_name,))
            columns = cur.fetchall()

            cur.execute("""
                SELECT a.attname AS column_name
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = %s::regclass AND i.indisprimary
            """, (f"public.{table_name}",))
            primary_key = [r["column_name"] for r in cur.fetchall()]

            cur.execute("""
                SELECT a.attname AS column_name, cf.relname AS references_table,
                       af.attname AS references_column
                FROM pg_constraint c
                JOIN pg_class cl ON cl.oid = c.conrelid
                JOIN pg_namespace n ON n.oid = cl.relnamespace
                JOIN pg_class cf ON cf.oid = c.confrelid
                JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = c.conkey[1]
                JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = c.confkey[1]
                WHERE c.contype = 'f' AND n.nspname = 'public' AND cl.relname = %s
            """, (table_name,))
            foreign_keys = cur.fetchall()

        return to_json({
            "table": table_name,
            "columns": columns,
            "primary_key": primary_key,
            "foreign_keys": foreign_keys,
        })
    finally:
        conn.close()


@mcp.tool()
def run_query(query: str) -> str:
    """Run a read-only SQL SELECT query against PostgreSQL and return the results as JSON.
    Only a single SELECT or WITH statement is allowed. Results are limited to 200 rows.
    Call get_schema first and use the exact table and column names it returns."""
    log.info("run_query called: %s", query)
    try:
        q = validate_query(query)
    except ValueError as e:
        log.warning("Rejected query: %s", e)
        return f"Query rejected: {e}"

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(q)
            rows = cur.fetchmany(MAX_ROWS + 1)
            truncated = len(rows) > MAX_ROWS
            rows = rows[:MAX_ROWS]
            columns = [d.name for d in cur.description] if cur.description else []
        conn.rollback()  # nothing to commit - read-only
        return to_json({
            "columns": columns,
            "row_count": len(rows),
            "truncated": truncated,
            "rows": rows,
        })
    except psycopg2.Error as e:
        log.error("Query failed: %s", e)
        return (f"Database error: {e.pgerror or str(e)}\n"
                "The query did NOT run, so there is no data yet. Call get_schema to check the exact "
                "table and column names, fix the SQL, and call run_query again.")
    finally:
        conn.close()


if __name__ == "__main__":
    log.info("Starting postgres-mcp server (stdio)")
    mcp.run()