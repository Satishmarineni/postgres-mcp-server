"""
test_tools.py
Calls the MCP tool functions directly to check results and security rules.
"""

from server import list_tables, describe_table, run_query

def section(title):
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)

# ---------- Normal use ----------
section("1. list_tables")
print(list_tables())

section("2. describe_table('appointments')")
print(describe_table("appointments"))

section("3. describe_table('unknown_table')")
print(describe_table("unknown_table"))

section("4. run_query - doctors per department")
print(run_query("""
    SELECT d.name AS department, COUNT(doc.doctor_id) AS doctors
    FROM departments d
    LEFT JOIN doctors doc ON doc.department_id = d.department_id
    GROUP BY d.name
    ORDER BY doctors DESC
"""))

section("5. run_query - claim totals by status")
print(run_query("""
    SELECT status, COUNT(*) AS claims, SUM(claim_amount) AS total_billed, SUM(paid_amount) AS total_paid
    FROM claims
    GROUP BY status
"""))

# ---------- Security checks (all should be rejected) ----------
attacks = [
    "DELETE FROM patients",
    "DROP TABLE patients",
    "SELECT * FROM patients; DROP TABLE patients",
    "UPDATE patients SET city = 'Hacked'",
    "SELECT * INTO stolen FROM patients",
    "WITH x AS (DELETE FROM claims RETURNING *) SELECT * FROM x",
]

section("6. Security checks - every query below should be REJECTED")
for q in attacks:
    result = run_query(q)
    status = "BLOCKED" if result.startswith(("Query rejected", "Database error")) else "!! ALLOWED !!"
    print(f"[{status}] {q}\n    -> {result}\n")

# Keywords inside text values should NOT be blocked
section("7. Keyword inside a string value (should be ALLOWED)")
print(run_query("SELECT COUNT(*) AS n FROM appointments WHERE reason = 'Do not delete'"))