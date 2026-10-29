"""
setup_db.py
Creates the mcp_demo database, a read-only user, and sample clinic data.
Safe to run multiple times - tables are dropped and recreated each run.
"""

import os
import random
from datetime import datetime, timedelta, date

import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

load_dotenv()

HOST = os.getenv("DB_HOST", "localhost")
PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "mcp_demo")
ADMIN_USER = os.getenv("DB_ADMIN_USER", "postgres")
ADMIN_PASSWORD = os.getenv("DB_ADMIN_PASSWORD", "postgres")
RO_USER = os.getenv("DB_USER", "mcp_readonly")
RO_PASSWORD = os.getenv("DB_PASSWORD", "readonly123")

random.seed(42)  # same data every run

# ---------------- Sample data ----------------
DEPARTMENTS = [
    ("Cardiology", 3),
    ("Orthopedics", 2),
    ("Pediatrics", 1),
    ("Neurology", 4),
    ("General Medicine", 1),
]

DOCTORS = [
    # first, last, specialty, department_id, hire_date
    ("Anita", "Rao", "Cardiologist", 1, "2015-06-01"),
    ("James", "Miller", "Cardiologist", 1, "2018-03-15"),
    ("Priya", "Sharma", "Orthopedic Surgeon", 2, "2016-09-20"),
    ("Robert", "Chen", "Orthopedic Surgeon", 2, "2020-01-10"),
    ("Maria", "Garcia", "Pediatrician", 3, "2017-11-05"),
    ("David", "Kim", "Neurologist", 4, "2019-07-22"),
    ("Sarah", "Johnson", "General Physician", 5, "2014-02-28"),
    ("Arjun", "Patel", "General Physician", 5, "2021-08-30"),
]

FIRST_NAMES = ["Liam", "Olivia", "Noah", "Emma", "Ravi", "Aisha", "Lucas", "Mia",
               "Ethan", "Sofia", "Kiran", "Chloe", "Mateo", "Grace", "Omar",
               "Zara", "Henry", "Lily", "Vikram", "Ella"]
LAST_NAMES = ["Smith", "Brown", "Wilson", "Reddy", "Nguyen", "Lopez", "Taylor",
              "Khan", "Anderson", "Thomas", "Martin", "Iyer", "Clark", "Lewis", "Young"]
CITIES = [("Boston", "MA"), ("Denver", "CO"), ("Phoenix", "AZ"),
          ("Atlanta", "GA"), ("Seattle", "WA"), ("Chicago", "IL")]
INSURERS = ["Blue Cross Blue Shield", "Aetna", "UnitedHealthcare",
            "Cigna", "Medicare", "Medicaid"]

REASONS = {
    1: ["Chest pain", "High blood pressure", "ECG follow-up", "Palpitations"],
    2: ["Knee pain", "Back pain", "Fracture follow-up", "Shoulder injury"],
    3: ["Vaccination", "Fever", "Annual checkup", "Ear infection"],
    4: ["Migraine", "Dizziness", "Numbness", "Seizure follow-up"],
    5: ["Annual physical", "Flu symptoms", "Diabetes check", "Cough"],
}

NUM_PATIENTS = 30
NUM_APPOINTMENTS = 60

# ---------------- Schema ----------------
SCHEMA_SQL = """
DROP TABLE IF EXISTS claims, appointments, patients, doctors, departments CASCADE;

CREATE TABLE departments (
    department_id SERIAL PRIMARY KEY,
    name          VARCHAR(100) NOT NULL UNIQUE,
    floor         INT
);

CREATE TABLE doctors (
    doctor_id     SERIAL PRIMARY KEY,
    first_name    VARCHAR(50) NOT NULL,
    last_name     VARCHAR(50) NOT NULL,
    specialty     VARCHAR(100),
    department_id INT REFERENCES departments(department_id),
    hire_date     DATE,
    email         VARCHAR(100) UNIQUE
);

CREATE TABLE patients (
    patient_id         SERIAL PRIMARY KEY,
    first_name         VARCHAR(50) NOT NULL,
    last_name          VARCHAR(50) NOT NULL,
    date_of_birth      DATE,
    gender             CHAR(1),
    city               VARCHAR(50),
    state              CHAR(2),
    insurance_provider VARCHAR(100),
    phone              VARCHAR(20)
);

CREATE TABLE appointments (
    appointment_id   SERIAL PRIMARY KEY,
    patient_id       INT REFERENCES patients(patient_id),
    doctor_id        INT REFERENCES doctors(doctor_id),
    appointment_date TIMESTAMP NOT NULL,
    status           VARCHAR(20) CHECK (status IN ('Scheduled','Completed','Cancelled','No-Show')),
    reason           VARCHAR(200)
);

CREATE TABLE claims (
    claim_id       SERIAL PRIMARY KEY,
    appointment_id INT REFERENCES appointments(appointment_id),
    claim_amount   NUMERIC(10,2) NOT NULL,
    paid_amount    NUMERIC(10,2) DEFAULT 0,
    status         VARCHAR(20) CHECK (status IN ('Submitted','Paid','Denied','Pending')),
    submitted_date DATE
);
"""


def create_database_and_role():
    """Connect to the default 'postgres' database to create mcp_demo and the read-only role."""
    conn = psycopg2.connect(host=HOST, port=PORT, dbname="postgres",
                            user=ADMIN_USER, password=ADMIN_PASSWORD)
    conn.autocommit = True  # CREATE DATABASE cannot run inside a transaction
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,))
        if cur.fetchone():
            print(f"Database '{DB_NAME}' already exists")
        else:
            cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DB_NAME)))
            print(f"Created database '{DB_NAME}'")

        cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (RO_USER,))
        action = "ALTER" if cur.fetchone() else "CREATE"
        cur.execute(sql.SQL(action + " ROLE {} WITH LOGIN PASSWORD {}").format(
            sql.Identifier(RO_USER), sql.Literal(RO_PASSWORD)))
        print(f"{'Updated' if action == 'ALTER' else 'Created'} read-only user '{RO_USER}'")
    conn.close()


def create_tables_and_data():
    conn = psycopg2.connect(host=HOST, port=PORT, dbname=DB_NAME,
                            user=ADMIN_USER, password=ADMIN_PASSWORD)
    cur = conn.cursor()

    # Tables
    cur.execute(SCHEMA_SQL)
    print("Created tables")

    # Departments
    cur.executemany("INSERT INTO departments (name, floor) VALUES (%s, %s)", DEPARTMENTS)

    # Doctors
    for first, last, specialty, dept_id, hire in DOCTORS:
        email = f"{first.lower()}.{last.lower()}@democlinic.org"
        cur.execute(
            """INSERT INTO doctors (first_name, last_name, specialty, department_id, hire_date, email)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (first, last, specialty, dept_id, hire, email))

    # Patients
    for _ in range(NUM_PATIENTS):
        city, state = random.choice(CITIES)
        dob = date(1950, 1, 1) + timedelta(days=random.randint(0, 365 * 70))
        cur.execute(
            """INSERT INTO patients (first_name, last_name, date_of_birth, gender,
                                     city, state, insurance_provider, phone)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (random.choice(FIRST_NAMES), random.choice(LAST_NAMES), dob,
             random.choice(["M", "F"]), city, state, random.choice(INSURERS),
             f"555-{random.randint(100, 999)}-{random.randint(1000, 9999)}"))

    # Appointments + claims
    now = datetime.now()
    start = datetime(2026, 1, 1)
    for _ in range(NUM_APPOINTMENTS):
        doctor_id = random.randint(1, len(DOCTORS))
        dept_id = DOCTORS[doctor_id - 1][3]
        appt_dt = start + timedelta(days=random.randint(0, 364), hours=random.randint(8, 16))

        if appt_dt > now:
            status = "Scheduled"
        else:
            status = random.choices(["Completed", "Cancelled", "No-Show"], weights=[75, 15, 10])[0]

        cur.execute(
            """INSERT INTO appointments (patient_id, doctor_id, appointment_date, status, reason)
               VALUES (%s, %s, %s, %s, %s) RETURNING appointment_id""",
            (random.randint(1, NUM_PATIENTS), doctor_id, appt_dt, status,
             random.choice(REASONS[dept_id])))
        appt_id = cur.fetchone()[0]

        # Only completed visits get a claim
        if status == "Completed":
            amount = round(random.uniform(100, 2500), 2)
            claim_status = random.choices(["Paid", "Pending", "Denied", "Submitted"],
                                          weights=[60, 20, 10, 10])[0]
            paid = round(amount * random.uniform(0.6, 0.9), 2) if claim_status == "Paid" else 0
            cur.execute(
                """INSERT INTO claims (appointment_id, claim_amount, paid_amount, status, submitted_date)
                   VALUES (%s, %s, %s, %s, %s)""",
                (appt_id, amount, paid, claim_status,
                 (appt_dt + timedelta(days=random.randint(1, 5))).date()))

    # Read-only permissions: SELECT only, nothing else
    ro = sql.Identifier(RO_USER)
    cur.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(DB_NAME), ro))
    cur.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(ro))
    cur.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(ro))
    cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {}").format(ro))
    print(f"Granted SELECT-only access to '{RO_USER}'")

    conn.commit()

    # Summary
    print("\nRow counts:")
    for table in ["departments", "doctors", "patients", "appointments", "claims"]:
        cur.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table)))
        print(f"  {table:<13} {cur.fetchone()[0]}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    create_database_and_role()
    create_tables_and_data()
    print("\nSetup complete!")