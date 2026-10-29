"""
chat_client.py
Terminal chat that answers questions about the mcp_demo database.
A local Ollama model decides which MCP tools to call; the postgres-mcp
server runs them against PostgreSQL (read-only).

Commands:  /reset  -> clear conversation   |   exit / quit  -> leave
"""

import os
import sys
import json
import asyncio

from ollama import AsyncClient
from mcp import Client, StdioServerParameters, stdio_client

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
MAX_TOOL_ROUNDS = 8           # stop runaway tool loops
MAX_TOOL_OUTPUT_CHARS = 8000  # keep small models' context from overflowing

SYSTEM_PROMPT = """You are a data assistant for a clinic's PostgreSQL database.

RULES:
1. At the start of a conversation, call get_schema. Use ONLY table and column names exactly as get_schema shows them. Never guess a column name.
2. Join tables only through the foreign keys (->) shown in the schema.
3. Apply every condition in the question (for example "completed" means status = 'Completed'; use the allowed values listed in the schema).
4. Write ONE PostgreSQL SELECT query and call run_query. Include counts or totals in the SELECT so you can report the numbers.
5. If run_query returns an error, you have NO data. Fix the SQL and call run_query again. Never answer with numbers that did not come from a successful run_query result.
6. Answer in plain English, briefly, with the key numbers.

The database is read-only. If the user asks to change or delete data, say you can't do that."""


def mcp_tools_to_ollama(tools) -> list[dict]:
    """Convert MCP tool definitions to the format Ollama expects."""
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description or "",
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


def result_to_text(result) -> str:
    """Pull the text out of an MCP tool result."""
    parts = [getattr(block, "text", "") for block in result.content]
    text = "\n".join(p for p in parts if p) or "(no output)"
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        text = text[:MAX_TOOL_OUTPUT_CHARS] + "\n... (output truncated)"
    return text


async def answer_question(question, messages, mcp_client, llm, tools) -> str:
    messages.append({"role": "user", "content": question})
    last_query_failed = False
    nudges = 0

    for _ in range(MAX_TOOL_ROUNDS):
        response = await llm.chat(model=MODEL, messages=messages, tools=tools,
                                  options={"temperature": 0})
        msg = response.message
        messages.append(msg)

        # No tool calls -> the model wants to give a final answer
        if not msg.tool_calls:
            # Guard: don't accept an answer right after a failed query (it would be made up)
            if last_query_failed and nudges < 2:
                nudges += 1
                print("  [guard] last query failed - asking the model to fix and retry")
                messages.append({"role": "user", "content":
                    "Your last query failed, so you have no data yet. Do not answer from memory. "
                    "Fix the SQL using the exact names from get_schema and call run_query again."})
                continue
            return msg.content or "(empty response)"

        for call in msg.tool_calls:
            name = call.function.name
            args = call.function.arguments or {}
            if isinstance(args, str):  # some models send arguments as a JSON string
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}

            print(f"  [tool] {name} {json.dumps(args)}")
            try:
                result = await mcp_client.call_tool(name, args)
                text = result_to_text(result)
            except Exception as e:
                text = f"Tool error: {e}"

            if name == "run_query":
                last_query_failed = text.startswith("Database error")

            messages.append({"role": "tool", "content": text, "tool_name": name})

    return "Stopped after too many tool calls. Try rephrasing the question."


async def main():
    server = StdioServerParameters(
        command=sys.executable,                      # the .venv Python running this script
        args=[os.path.join(BASE_DIR, "server.py")],
        cwd=BASE_DIR,
    )
    llm = AsyncClient()

    # Server log lines go to server.log so they don't clutter the chat
    with open(os.path.join(BASE_DIR, "server.log"), "a", encoding="utf-8") as errlog:
        async with Client(stdio_client(server, errlog=errlog)) as mcp_client:
            listing = await mcp_client.list_tools()
            tools = mcp_tools_to_ollama(listing.tools)

            print(f"Connected to postgres-mcp | model: {MODEL}")
            print(f"Tools: {', '.join(t.name for t in listing.tools)}")
            print("Ask a question about the clinic database. Type 'exit' to quit, '/reset' to clear history.")

            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

            while True:
                question = (await asyncio.to_thread(input, "\nYou: ")).strip()
                if not question:
                    continue
                if question.lower() in {"exit", "quit"}:
                    break
                if question == "/reset":
                    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                    print("History cleared.")
                    continue

                try:
                    reply = await answer_question(question, messages, mcp_client, llm, tools)
                except ConnectionError:
                    reply = "Can't reach Ollama. Is it running? (open the Ollama app or run 'ollama serve')"
                except Exception as e:
                    reply = f"Error: {e}"

                print(f"\nAssistant: {reply}")


if __name__ == "__main__":
    asyncio.run(main())