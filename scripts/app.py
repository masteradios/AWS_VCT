"""
Esports SQL assistant
  - Data source : AWS Glue Data Catalog tables, queried through Amazon Athena
  - LLM         : Amazon Bedrock (Converse API with tool use)
  - UI          : Streamlit

Run:  streamlit run app.py
"""
import os
import re

import awswrangler as wr
import boto3
import pandas as pd
import streamlit as st
from botocore.config import Config
from dotenv import load_dotenv

load_dotenv()

# ----------------------------------------------------------------------------
# Configuration (override via environment variables or a .env file)
# ----------------------------------------------------------------------------
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
GLUE_DATABASE = os.getenv("GLUE_DATABASE", "aws_vct")
ATHENA_WORKGROUP = os.getenv("ATHENA_WORKGROUP", "primary")
ATHENA_OUTPUT_S3 = os.getenv("ATHENA_OUTPUT_S3","s3://aws-vct/athenaLogs/")  # optional, e.g. s3://my-bucket/athena-results/
# Use the model ID or inference profile ID that is enabled in YOUR account/region
# (Bedrock console -> Model access / Cross-region inference).
BEDROCK_MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "amazon.nova-pro-v1:0")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@example.com")
MAX_TOOL_STEPS = 6          # max run_sql attempts per question
ROWS_SENT_TO_LLM = 50       # rows of each result shown to the model
ALLOWED_TABLES = [
    "games", "teams", "game_team_event", "game_players_event", "game_rounds_event",
    "game_damage_event", "game_game_decided_event", "game_player_died_event",
    "game_spike_status_event",
]

# ----------------------------------------------------------------------------
# AWS clients
# ----------------------------------------------------------------------------
@st.cache_resource
def get_session() -> boto3.Session:
    # Picks up AWS_PROFILE / env credentials / IAM role automatically
    return boto3.Session(region_name=AWS_REGION)


@st.cache_resource
def get_bedrock():
    return get_session().client(
        "bedrock-runtime",
        config=Config(read_timeout=120, retries={"max_attempts": 3, "mode": "standard"}),
    )


# ----------------------------------------------------------------------------
# Athena execution + read-only guard + Athena dialect guard
# ----------------------------------------------------------------------------
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|merge|grant|revoke|"
    r"unload|call|msck|repair|vacuum|optimize)\b",
    re.IGNORECASE,
)

# Syntax that works in Postgres/MySQL/SQL Server but NOT in Athena (Trino).
# Each entry is (regex, message returned to the LLM so it can fix the query and retry).
DIALECT_ISSUES = [
    (r"\bilike\b",
     "ILIKE is not supported in Athena. Use lower(col) LIKE '%text%'."),
    (r"::",
     "The :: cast operator is not supported in Athena. Use CAST(x AS type)."),
    (r"`",
     "Backticks are not supported in Athena. Use unquoted lowercase identifiers."),
    (r"\b(string_agg|group_concat|listagg)\s*\(",
     "string_agg/group_concat/listagg are not supported. Use array_join(array_agg(x), ', ')."),
    (r"\b(ifnull|isnull|nvl)\s*\(",
     "ifnull/isnull/nvl are not supported. Use COALESCE(x, y)."),
    (r"\blen\s*\(",
     "len() is not supported. Use length(x)."),
    (r"\bselect\s+top\s+\d+",
     "TOP is not supported. Use LIMIT n at the end of the query."),
    (r"\b(datediff|dateadd)\s*\(",
     "datediff/dateadd are not supported. Use date_diff('second', t1, t2) / date_add('minute', n, t)."),
    (r"\bto_timestamp\s*\(",
     "to_timestamp() is not available. walltime is a string: use CAST(walltime AS timestamp)."),
    (r"\bto_date\s*\(",
     "to_date() is not available. Use CAST(x AS date) or date(CAST(walltime AS timestamp))."),
    (r"\barray_contains\s*\(",
     "array_contains() is not supported. Use contains(array_col, 'value')."),
    (r"\bgenerate_series\s*\(",
     "generate_series() is not supported. Use sequence(start, stop)."),
    (r"\blimit\s+\d+\s+offset\s+\d+",
     "In Athena OFFSET comes before LIMIT: ... OFFSET n LIMIT m."),
    (r"\bcast\s*\(\s*(?:\w+\.)?roundnumber\s+as\s+(?:integer|int|bigint)\s*\)",
     "roundnumber is stored as text like '3.0' (or empty), so a direct integer cast fails. "
     "Use TRY(CAST(CAST(roundnumber AS double) AS integer))."),
]
_DIALECT_COMPILED = [(re.compile(p, re.IGNORECASE | re.DOTALL), msg) for p, msg in DIALECT_ISSUES]

_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


def _strip_literals(sql: str) -> str:
    """Replace '...' string literals with '' so keyword checks don't trip on data values."""
    return _STRING_LITERAL.sub("''", sql)


def validate_sql(sql: str) -> str:
    """Return cleaned SQL or raise ValueError if it is not a single read-only, Athena-compatible SELECT."""
    sql = sql.strip().rstrip(";").strip()
    no_lit = _strip_literals(sql)

    if "--" in no_lit or "/*" in no_lit:
        raise ValueError("SQL comments are not allowed. Remove them and retry.")
    if ";" in no_lit:
        raise ValueError("Only one statement is allowed.")
    if not re.match(r"^(select|with)\b", sql, re.IGNORECASE):
        raise ValueError("Only read-only SELECT queries are allowed.")
    if FORBIDDEN.search(no_lit):
        raise ValueError("Query contains a forbidden keyword. Only read-only SELECTs are allowed.")
    if re.search(r"information_schema|pg_catalog", no_lit, re.IGNORECASE):
        raise ValueError("Querying system catalogs is not allowed.")

    for pattern, message in _DIALECT_COMPILED:
        if pattern.search(no_lit):
            raise ValueError(f"Not valid Athena (Trino) SQL: {message}")
    return sql


def run_athena(sql: str) -> pd.DataFrame:
    return wr.athena.read_sql_query(
        sql=sql,
        database=GLUE_DATABASE,
        workgroup=ATHENA_WORKGROUP,
        s3_output=ATHENA_OUTPUT_S3,
        ctas_approach=False,      # plain SELECT, no temp tables created
        boto3_session=get_session(),
    )


# ----------------------------------------------------------------------------
# Schema + known values pulled live from Glue / Athena
# ----------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="Reading Glue catalog...")
def load_schema_from_glue() -> str:
    glue = get_session().client("glue")
    tables = []
    for page in glue.get_paginator("get_tables").paginate(DatabaseName=GLUE_DATABASE):
        tables.extend(page["TableList"])

    wanted = [t for t in tables if t["Name"].lower() in ALLOWED_TABLES] or tables
    lines = []
    for t in sorted(wanted, key=lambda x: x["Name"]):
        cols = t.get("StorageDescriptor", {}).get("Columns", []) + t.get("PartitionKeys", [])
        col_txt = ", ".join(f"{c['Name'].lower()} {c['Type']}" for c in cols)
        lines.append(f"{t['Name'].lower()}({col_txt})")
    return "\n".join(lines)


@st.cache_data(ttl=3600, show_spinner="Loading known names...")
def load_known_values() -> str:
    queries = {
        "league_name": "SELECT DISTINCT league_name FROM games ORDER BY 1",
        "tournament_name": "SELECT DISTINCT tournament_name FROM games ORDER BY 1",
        "team_name": "SELECT DISTINCT name FROM teams ORDER BY 1",
    }
    out = []
    for label, sql in queries.items():
        try:
            df = run_athena(sql)
            vals = [str(v) for v in df.iloc[:, 0].dropna().tolist()]
            out.append(f"{label}: {' | '.join(vals)}")
        except Exception as e:  # keep app usable if one lookup fails
            out.append(f"{label}: (could not load: {e})")
    return "\n".join(out)


# ----------------------------------------------------------------------------
# System prompt (Athena / Trino dialect)
# ----------------------------------------------------------------------------
def build_system_prompt() -> str:
    return f"""
You are a SQL assistant for esports match data stored in AWS Glue tables and queried with Amazon Athena (Trino SQL).

RULES
- Answer every data question by calling the run_sql tool. Do not ask the user for the schema; it is below.
- Write ONE read-only SELECT (CTEs allowed). Never INSERT/UPDATE/DELETE/DROP/CREATE/ALTER.
  If asked to modify data or the database, strictly refuse and do not provide any such query.
- Use only the tables listed below. Never query information_schema.
- Use lowercase, unquoted identifiers. Do not prefix table names with a database name.
- Do not use SQL comments (-- or /* */) and do not end the query with a semicolon.
- If run_sql returns an error, read it, fix the query, and retry.
- Add LIMIT 100 to non-aggregate queries.
- Show players by handle and teams by team_name in results, never by raw key.
- After you have the data, answer briefly in plain language. Do not repeat the full table;
  the UI already displays it.

ATHENA / TRINO DIALECT (important - this is NOT Postgres, MySQL or SQL Server)
- There is NO ILIKE and NO :: casting.
  Case-insensitive match:  lower(col) LIKE '%text%'
  Casting:                 CAST(col AS integer), CAST(col AS double), CAST(col AS varchar)
  Safe casting (NULL instead of error):  TRY(CAST(col AS double))  or  TRY_CAST(col AS double)
- Arrays are expanded with:  CROSS JOIN UNNEST(array_col) AS a(pkey)
- Array helpers: cardinality(arr), contains(arr, value), element_at(arr, 1), array_join(arr, ', ').
  Do NOT use array_contains, array_length, size or LIKE on arrays.
- String concat uses ||. Use COUNT(*), COUNT(DISTINCT x), approx_distinct is also available.
- Use COALESCE (not ifnull/isnull/nvl), length() (not len), LIMIT n (not TOP n),
  array_join(array_agg(x), ', ') (not string_agg/group_concat).
- Date/time functions: date_trunc('hour', ts), date_diff('second', ts1, ts2), date_add('minute', 5, ts),
  date(ts), year(ts), hour(ts). Not datediff/dateadd/to_timestamp/to_date.
- Timestamp literals are written TIMESTAMP '2023-09-27 00:00:00'; date literals DATE '2023-09-27'.
- OFFSET goes before LIMIT:  OFFSET 10 LIMIT 5
- Integer division truncates: use CAST(x AS double) / y for ratios and percentages.
- Both sides of a comparison/join must have the same type; check the column types below and CAST when they differ.

TABLES (live from the Glue catalog)
{load_schema_from_glue()}

CONCEPTS
- platformgameid identifies one game played. walltime is when an event happened.
- team_key and player_key are LOCAL to one game. Team XYZ may be team_key=1 in game A and
  team_key=2 in game B. team_id and player_id are GLOBAL and stable across games.
  To count or group across games, group by team_id / player_id.
- game_rounds_event: when round_status = 'DECIDED', the cause column explains how it ended.
- game_game_decided_event has one row per finished game and holds the game winner.
- The games table says which league or tournament a particular game belongs to.

TIMESTAMPS (critical)
- walltime is stored as a STRING in the format 'yyyy-MM-dd HH:mm:ss.SSS' (UTC),
  for example '2023-09-27 23:09:59.312'. It is NOT a timestamp column, whatever else you assume.
- ALWAYS convert it before any time logic (comparing, filtering, ORDER BY, MIN/MAX, window ordering,
  date_trunc, date_diff, extracting dates or hours):
    CAST(walltime AS timestamp)
- Compare against timestamp literals, never against plain strings:
    CAST(walltime AS timestamp) >= TIMESTAMP '2023-09-27 00:00:00'
    AND CAST(walltime AS timestamp) < TIMESTAMP '2023-09-28 00:00:00'
- Date only:  date(CAST(walltime AS timestamp))
- Durations:  date_diff('second', MIN(CAST(walltime AS timestamp)), MAX(CAST(walltime AS timestamp)))
- If you get a cast error (some rows may be empty or in the older ISO form '2023-09-27T23:09:59.312Z'), use:
    COALESCE(TRY(CAST(walltime AS timestamp)), TRY(CAST(from_iso8601_timestamp(walltime) AS timestamp)))
- Never apply date/time functions directly to the raw walltime string.

ASSISTANTS (game_player_died_event) - a pipe-delimited STRING, not an array column
- assistants is a STRING of player_key values (local to the game) joined by '|', for example '3|5'.
  A kill with no assistants has an empty string (or NULL).
- Turn it into a real array with this exact expression and use it everywhere assistants is needed:
    filter(split(COALESCE(assistants, ''), '|'), x -> x <> '')
  The filter is required because split('', '|') returns one empty element.
- Number of assistants on a kill:
    cardinality(filter(split(COALESCE(assistants, ''), '|'), x -> x <> ''))
- Kills where player_key '3' assisted:
    contains(filter(split(COALESCE(assistants, ''), '|'), x -> x <> ''), '3')
- One row per assistant (kills with no assistants disappear, which is right for assist counts):
    CROSS JOIN UNNEST(filter(split(COALESCE(d.assistants, ''), '|'), x -> x <> '')) AS a(pkey)
- To keep kills that have no assistants, use LEFT JOIN UNNEST(...) AS a(pkey) ON TRUE (pkey is NULL for them).
- pkey is a varchar, so join it directly: p.player_key = a.pkey AND p.platformgameid = d.platformgameid.
- Never use LIKE on assistants to find a player: LIKE '%3%' also matches 13 and 30.

ROUND NUMBERS
- roundnumber is text. In event tables (damage, player died, spike status) it can look like '3.0',
  or be empty/NULL when the event happened outside a round. In game_rounds_event it can look like '3'.
- A direct CAST(roundnumber AS integer) FAILS on '3.0'. Always use:
    TRY(CAST(CAST(roundnumber AS double) AS integer))
- When joining or comparing roundnumber across tables, normalise BOTH sides that way.
- Exclude events with no round when grouping by round:  WHERE roundnumber IS NOT NULL AND roundnumber <> ''

NAME MATCHING
- When the user gives any name (player, team, league, tournament), never use = ;
  use lower(column) LIKE '%name%' (lowercase the pattern).
- A player name can be a handle, first name, last name, or full name. Search all with OR:
    (lower(handle) LIKE '%x%' OR lower(first_name) LIKE '%x%' OR lower(last_name) LIKE '%x%'
     OR lower(first_name || ' ' || last_name) LIKE '%x%')
- A team name can be a full name or short form. Match against team_name in game_team_event.
  Tournament and league names go against tournament_name / league_name in games.
- Apply the name filter to the deduped player/team subquery, then group by player_id / team_id.
  Do not filter on player_key or team_key (local to one game).
- A partial match can hit several people. Always group by player_id AND handle.
- Users rarely type a name exactly as stored. Never put the whole phrase in one LIKE:
  1. Drop generic words: league, tournament, team, player, game(s), the, of, in, a, championship, cup.
  2. Require EACH remaining word separately with AND:
       lower(league_name) LIKE '%game%' AND lower(league_name) LIKE '%changers%'
  3. If the wording is ambiguous or the first query returns 0 rows, do not answer "none".
     Run SELECT DISTINCT league_name FROM games WHERE lower(league_name) LIKE '%<keyword>%'
     (same for tournament_name, team_name, handle), look at real values, then rerun.
- If several stored names match, say which matched and report results per name.

KNOWN VALUES (exact strings stored in the data)
{load_known_values()}
When the user mentions a league, tournament, or team, pick the closest stored value above
and filter with it (still using lower(...) LIKE).

JOIN RULES (critical)
- Because keys are local, EVERY join to game_players_event or game_team_event must include
  platformgameid in addition to the key:
    players: gp.platformgameid = x.platformgameid AND gp.player_key = x.<id column>
    teams:   gt.platformgameid = x.platformgameid AND gt.team_key = x.<team column>
- Player columns (match gp.player_key): killer_id, deceased_id, causer_id, victim_id,
  carrier, and each item of the split assistants string.
- Team columns (match gt.team_key): attackingteam, defendingteam, attacking_team,
  defending_team, winning_team.
- Round-level joins between event tables use platformgameid AND the normalised roundnumber
  (see ROUND NUMBERS).
- game_players_event can have several rows per player per game. Dedupe first:
  (SELECT DISTINCT platformgameid, player_key, player_id, handle, first_name, last_name FROM game_players_event)

TYPES
- Most columns are strings. Check the column types listed in TABLES before doing math.
- winning_round_number, damage_amount may be strings. Cast before math/ordering:
  CAST(damage_amount AS double), TRY(CAST(winning_round_number AS integer)).
  For roundnumber use the double-then-integer cast described in ROUND NUMBERS.
- kill_event: if the schema shows boolean, use kill_event = true. If it shows string, values are
  'True' / 'False', so use lower(kill_event) = 'true'.

EXAMPLE QUERIES
Q: Who has the most kills?
SELECT p.player_id, p.handle, COUNT(*) AS kills
FROM game_player_died_event d
JOIN (SELECT DISTINCT platformgameid, player_key, player_id, handle FROM game_players_event) p
  ON p.platformgameid = d.platformgameid AND p.player_key = d.killer_id
GROUP BY p.player_id, p.handle ORDER BY kills DESC LIMIT 10

Q: Total damage per player
SELECT p.player_id, p.handle, SUM(CAST(g.damage_amount AS double)) AS total_damage
FROM game_damage_event g
JOIN (SELECT DISTINCT platformgameid, player_key, player_id, handle FROM game_players_event) p
  ON p.platformgameid = g.platformgameid AND p.player_key = g.causer_id
GROUP BY p.player_id, p.handle ORDER BY total_damage DESC LIMIT 100

Q: Which team won the most games?
SELECT t.team_id, t.team_name, COUNT(*) AS wins
FROM game_game_decided_event gd
JOIN (SELECT DISTINCT platformgameid, team_key, team_id, team_name FROM game_team_event) t
  ON t.platformgameid = gd.platformgameid AND t.team_key = gd.winning_team
GROUP BY t.team_id, t.team_name ORDER BY wins DESC LIMIT 100

Q: Who has the most assists?
SELECT p.player_id, p.handle, COUNT(*) AS assists
FROM game_player_died_event d
CROSS JOIN UNNEST(filter(split(COALESCE(d.assistants, ''), '|'), x -> x <> '')) AS a(pkey)
JOIN (SELECT DISTINCT platformgameid, player_key, player_id, handle FROM game_players_event) p
  ON p.platformgameid = d.platformgameid AND p.player_key = a.pkey
GROUP BY p.player_id, p.handle ORDER BY assists DESC LIMIT 10

Q: What is the average number of assistants per kill?
SELECT AVG(CAST(cardinality(filter(split(COALESCE(assistants, ''), '|'), x -> x <> '')) AS double)) AS avg_assistants_per_kill
FROM game_player_died_event

Q: How many kills happened in each round of a game?
SELECT TRY(CAST(CAST(roundnumber AS double) AS integer)) AS round_no, COUNT(*) AS kills
FROM game_player_died_event
WHERE platformgameid = '<platformgameid>' AND roundnumber IS NOT NULL AND roundnumber <> ''
GROUP BY 1 ORDER BY 1

Q: How many games finished on 27 September 2023?
SELECT COUNT(*) AS games
FROM game_game_decided_event
WHERE CAST(walltime AS timestamp) >= TIMESTAMP '2023-09-27 00:00:00'
  AND CAST(walltime AS timestamp) < TIMESTAMP '2023-09-28 00:00:00'

Q: How long did each game last?
SELECT platformgameid,
       date_diff('minute', MIN(CAST(walltime AS timestamp)), MAX(CAST(walltime AS timestamp))) AS minutes
FROM game_rounds_event
GROUP BY platformgameid ORDER BY minutes DESC LIMIT 100

Q: How many kills does Zekken have?
SELECT p.player_id, p.handle, COUNT(*) AS kills
FROM game_player_died_event d
JOIN (SELECT DISTINCT platformgameid, player_key, player_id, handle, first_name, last_name
      FROM game_players_event) p
  ON p.platformgameid = d.platformgameid AND p.player_key = d.killer_id
WHERE (lower(p.handle) LIKE '%zekken%' OR lower(p.first_name) LIKE '%zekken%'
       OR lower(p.last_name) LIKE '%zekken%'
       OR lower(p.first_name || ' ' || p.last_name) LIKE '%zekken%')
GROUP BY p.player_id, p.handle

Q: How many games were played in game changers league?
SELECT league_name, COUNT(*) AS games
FROM games
WHERE lower(league_name) LIKE '%game%' AND lower(league_name) LIKE '%changers%'
GROUP BY league_name
""".strip()


TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "run_sql",
                "description": "Run one read-only Athena (Trino) SELECT query against the esports "
                               "Glue database and return the rows. Must be valid Athena/Trino SQL: "
                               "walltime is a string (CAST(walltime AS timestamp)), assistants is a pipe-delimited string like '3|5'.",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {"sql": {"type": "string", "description": "A single SELECT query."}},
                        "required": ["sql"],
                    }
                },
            }
        }
    ]
}


# ----------------------------------------------------------------------------
# Agent loop
# ----------------------------------------------------------------------------
def ask_agent(question: str, history: list[dict]) -> tuple[str, list[dict]]:
    """
    history: previous turns as [{'role': 'user'|'assistant', 'content': str}, ...]
    Returns (answer_text, steps) where steps = [{'sql', 'df', 'error'}, ...]
    """
    bedrock = get_bedrock()
    system = [{"text": build_system_prompt()}]

    messages = [{"role": h["role"], "content": [{"text": h["content"]}]} for h in history]
    messages.append({"role": "user", "content": [{"text": question}]})

    steps: list[dict] = []

    for _ in range(MAX_TOOL_STEPS + 1):
        resp = bedrock.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=messages,
            system=system,
            toolConfig=TOOL_CONFIG,
            inferenceConfig={"temperature": 0.1, "maxTokens": 2048},
        )
        msg = resp["output"]["message"]
        messages.append(msg)

        if resp["stopReason"] != "tool_use":
            text = "".join(b.get("text", "") for b in msg["content"]).strip()
            return text or "(no answer returned)", steps

        tool_results = []
        for block in msg["content"]:
            if "toolUse" not in block:
                continue
            tu = block["toolUse"]
            sql = tu["input"].get("sql", "")
            step = {"sql": sql, "df": None, "error": None}
            try:
                clean = validate_sql(sql)
                df = run_athena(clean)
                step["df"] = df
                if df.empty:
                    payload = "Query ran successfully but returned 0 rows."
                else:
                    payload = (
                        f"{len(df)} rows total. Showing first {min(len(df), ROWS_SENT_TO_LLM)}:\n"
                        + df.head(ROWS_SENT_TO_LLM).to_csv(index=False)
                    )
                status = "success"
            except Exception as e:
                step["error"] = str(e)
                payload = f"ERROR: {e}"
                status = "error"
            steps.append(step)
            tool_results.append(
                {"toolResult": {"toolUseId": tu["toolUseId"],
                                "content": [{"text": payload}],
                                "status": status}}
            )

        messages.append({"role": "user", "content": tool_results})

    return "I couldn't get a working query within the retry limit. Try rephrasing the question.", steps


# ----------------------------------------------------------------------------
# Streamlit UI
# ----------------------------------------------------------------------------
st.set_page_config(page_title="Esports Data Assistant", page_icon="🎮", layout="wide")
st.title("🎮 Esports Data Assistant")

with st.sidebar:
    st.header("Session")
    email = st.text_input("Email", value="guest@example.com")
    is_admin = email.strip().lower() == ADMIN_EMAIL.lower()
    st.caption(f"Role: **{'admin' if is_admin else 'user'}**")
    st.caption("Note: this is a convenience switch, not real authentication. "
               "Put the app behind SSO / Cognito for production.")
    st.divider()
    st.caption(f"Region: `{AWS_REGION}`")
    st.caption(f"Glue DB: `{GLUE_DATABASE}`")
    st.caption(f"Workgroup: `{ATHENA_WORKGROUP}`")
    st.caption(f"Model: `{BEDROCK_MODEL_ID}`")
    if st.button("Clear chat"):
        st.session_state.chat = []
        st.rerun()
    if st.button("Refresh schema / names"):
        st.cache_data.clear()
        st.rerun()

if "chat" not in st.session_state:
    st.session_state.chat = []


def render_steps(steps: list[dict]):
    for i, s in enumerate(steps, 1):
        if is_admin:
            with st.expander(f"SQL #{i}" + (" - error" if s["error"] else ""), expanded=False):
                st.code(s["sql"], language="sql")
                if s["error"]:
                    st.error(s["error"])
    # Show the last successful result to everyone
    ok = [s for s in steps if s["df"] is not None and not s["df"].empty]
    if ok:
        df = ok[-1]["df"]
        st.dataframe(df, use_container_width=True)
        # Simple auto-chart: one label column + one numeric column
        if df.shape[1] == 2 and len(df) <= 50 and pd.api.types.is_numeric_dtype(df.iloc[:, 1]) \
                and not pd.api.types.is_numeric_dtype(df.iloc[:, 0]):
            st.bar_chart(df.set_index(df.columns[0]))


for m in st.session_state.chat:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("steps"):
            render_steps(m["steps"])

if question := st.chat_input("Ask about players, teams, leagues, matches..."):
    with st.chat_message("user"):
        st.markdown(question)

    history = [{"role": m["role"], "content": m["content"]} for m in st.session_state.chat]
    st.session_state.chat.append({"role": "user", "content": question})

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                answer, steps = ask_agent(question, history)
            except Exception as e:
                answer, steps = f"Something went wrong calling AWS: `{e}`", []
        st.markdown(answer)
        render_steps(steps)

    st.session_state.chat.append({"role": "assistant", "content": answer, "steps": steps})