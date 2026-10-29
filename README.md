\# PostgreSQL MCP Server



An MCP (Model Context Protocol) server that lets an AI assistant answer plain-English questions about a PostgreSQL database. It's built with safety in mind: the AI can read data but can never change or delete it.



The project includes a sample clinic database (departments, doctors, patients, appointments, insurance claims) and a terminal chat client that runs a local LLM through Ollama.



\## How it works



```

You: "What's the total billed vs. paid amount for claims by status?"

&#x20;       │

&#x20;       ▼

&#x20;chat\_client.py ──► Ollama (qwen2.5:7b) picks a tool and writes the SQL

&#x20;       │

&#x20;       ▼  MCP over stdio

&#x20;server.py ──► PostgreSQL (read-only user)

&#x20;       │

&#x20;       └── results go back to the model ──► plain-English answer

```



The model never connects to the database itself. It can only call the four tools the server exposes.



\## Tools



| Tool | What it does |

|---|---|

| `get\_schema` | Returns every table, column, primary key, foreign key and allowed value in one call |

| `list\_tables` | Lists tables with row counts |

| `describe\_table` | Shows details for one table |

| `run\_query` | Runs a single read-only `SELECT` and returns up to 200 rows as JSON |



\## Security



Letting an LLM write SQL is risky, so the server uses several independent layers. Any one of them on its own would block a destructive query.



1\. \*\*Read-only database user.\*\* The server logs in as `mcp\_readonly`, which only has `SELECT` permission. PostgreSQL itself rejects `INSERT`, `UPDATE`, `DELETE` and `DROP`.

2\. \*\*Read-only transaction.\*\* Every connection is opened in read-only mode.

3\. \*\*SQL validation.\*\* Only a single `SELECT` or `WITH` statement is allowed. Stacked statements (`SELECT ...; DROP ...`), `SELECT INTO`, and write keywords hidden inside a `WITH` are rejected. Words inside string values (e.g. `'Do not delete'`) are ignored, so normal queries aren't blocked by mistake.

4\. \*\*Limits.\*\* Each query has a 5-second timeout and returns at most 200 rows.

5\. \*\*Secrets stay out of code.\*\* Credentials are loaded from a `.env` file that is excluded from Git.



`test\_tools.py` includes attack queries that check these rules.



\## Making a small local model reliable



The first version gave wrong answers in a way that's easy to miss. The 7B model guessed column names (for example `billed\_amount` instead of `claim\_amount`), the query failed, and the model then replied with numbers it made up. It sounded confident and was completely wrong.



What fixed it:



\- \*\*`get\_schema` tool.\*\* The model sees the real schema, foreign keys and allowed status values before writing SQL, instead of guessing.

\- \*\*Clearer error messages.\*\* When a query fails, the server tells the model that no data was returned and what to do next.

\- \*\*A guard in the client.\*\* If the last query failed and the model tries to answer anyway, the client sends it back to fix the SQL (up to two retries) instead of showing a made-up answer.

\- \*\*Stricter system prompt and temperature 0.\*\* This makes the SQL more consistent and makes sure filters like "completed" are applied.



After these changes, the same questions return results that match the database.



\## Tech stack



\- Python 3.12, managed with `uv`

\- MCP Python SDK v2 (`MCPServer` for the server, `Client` for the chat client)

\- PostgreSQL 18 with `psycopg2`

\- Ollama with `qwen2.5:7b` for local tool calling

\- MCP Inspector for testing



\## Project structure



```

postgres-mcp/

├── server.py          # MCP server with the 4 tools and security checks

├── chat\_client.py     # Terminal chat: Ollama + MCP client

├── setup\_db.py        # Creates the database, read-only user and sample data

├── test\_tools.py      # Tool and security tests

├── requirements.txt

├── .env.example       # Template for credentials (copy to .env)

└── .gitignore

```



\## Setup (Windows)



\*\*Prerequisites:\*\* Python 3.11+ (or `uv`), PostgreSQL, Ollama, and Node.js (only needed for MCP Inspector).



```cmd

:: 1. Create and activate the environment

uv venv --python 3.12

.venv\\Scripts\\activate.bat

uv pip install -r requirements.txt



:: 2. Configure credentials

copy .env.example .env

:: then edit .env with your PostgreSQL password



:: 3. Create the database, read-only user and sample data

python setup\_db.py



:: 4. Run the tests

python test\_tools.py



:: 5. Pull a model that supports tool calling

ollama pull qwen2.5:7b

```



\## Usage



\*\*Chat in plain English\*\*

```cmd

python chat\_client.py

```

Example questions:

\- How many patients does each insurance provider have?

\- What's the total billed vs. paid amount for claims by status?

\- Which doctor has the most completed appointments?

\- Which department earned the most from paid claims?

\- Delete all patients. \*(refused)\*



The client prints each tool call and the SQL the model wrote, so you can check how every answer was produced.



\*\*Inspect the server with MCP Inspector\*\*

```cmd

npx @modelcontextprotocol/inspector .venv\\Scripts\\python.exe server.py

```



\## Limitations



\- Tested only with the sample database. A larger schema would need a smarter way to pick which tables to show the model.

\- A 7B model can still write wrong but valid SQL, so a query can run and still answer a slightly different question. The printed SQL makes this easy to spot.

\- `LIMIT 1` style answers can hide ties.

\- The server runs locally over stdio and has no user authentication.



\## Next steps



\- Switch the server to Streamable HTTP and run it in Docker

\- Wrap the chat logic in a FastAPI endpoint with a simple web UI

\- Deploy on AWS: RDS for PostgreSQL, ECS Fargate for the server and API, Bedrock for the LLM, Secrets Manager for credentials, and Cognito for login

\- Add row-level security so each customer only sees their own data

