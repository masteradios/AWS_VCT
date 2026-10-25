import asyncio
import uuid
import psycopg2
from vanna import Agent, AgentConfig, SystemPromptBuilder, ToolContext
from vanna.core.registry import ToolRegistry
from vanna.core.user import UserResolver, User, RequestContext
from vanna.tools import RunSqlTool, VisualizeDataTool
from vanna.core.agent.config import AgentConfig, UiFeatures, UiFeature

from vanna.tools.agent_memory import (
    SaveQuestionToolArgsTool, 
    SearchSavedCorrectToolUsesTool, 
    SaveTextMemoryTool
)

from vanna.servers.fastapi import VannaFastAPIServer
from vanna.integrations.ollama import OllamaLlmService
from vanna.integrations.postgres import PostgresRunner
from vanna.integrations.local.agent_memory import DemoAgentMemory

# 1. Configure your LLM
llm = OllamaLlmService(
    model="qwen2.5:14b",
    host="http://localhost:11434"
)

# 2. Configure your database
db_tool = RunSqlTool(
    sql_runner=PostgresRunner(
        connection_string="postgresql://postgres:postgres@localhost:5432/aws_vct"
    )
)

# 3. Configure your agent memory
agent_memory = DemoAgentMemory(max_items=1000)

# 4. Configure user authentication
# class SimpleUserResolver(UserResolver):
#     async def resolve_user(self, request_context: RequestContext) -> User:
#         user_email = request_context.get_cookie('vanna_email') or 'admin@example.com'
#         group = 'admin' if user_email == 'admin@example.com' else 'user'
#         return User(id=user_email, email=user_email, group_memberships=[group])

class SimpleUserResolver(UserResolver):
    async def resolve_user(self, request_context: RequestContext) -> User:
        user_email = request_context.get_cookie('vanna_email') or 'guest@example.com'
        group = 'admin' if user_email == 'admin@example.com' else 'user'
        return User(id=user_email, email=user_email, group_memberships=[group])

user_resolver = SimpleUserResolver()

# 5. Create your tools registry
tools = ToolRegistry()
tools.register_local_tool(db_tool, access_groups=['admin', 'user'])
tools.register_local_tool(SaveQuestionToolArgsTool(), access_groups=['admin'])
tools.register_local_tool(SearchSavedCorrectToolUsesTool(), access_groups=['admin', 'user'])
tools.register_local_tool(SaveTextMemoryTool(), access_groups=['admin', 'user'])
tools.register_local_tool(VisualizeDataTool(), access_groups=['admin', 'user'])

# # 6. Create your agent
# agent = Agent(
#     llm_service=llm,
#     tool_registry=tools,
#     user_resolver=user_resolver,
#     agent_memory=agent_memory
# )


def load_names():
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/aws_vct")
    cur = conn.cursor()
    out = {}
    for label, sql in [
        ("league_name", "SELECT DISTINCT league_name FROM games ORDER BY 1"),
        ("tournament_name", "SELECT DISTINCT tournament_name FROM games ORDER BY 1"),
        ("team_name", "SELECT DISTINCT name FROM teams ORDER BY 1"),
    ]:
        cur.execute(sql)
        out[label] = [r[0] for r in cur.fetchall() if r[0]]
    conn.close()
    return out

names = load_names()
KNOWN_VALUES = "\n".join(f"{k}: {' | '.join(v)}" for k, v in names.items())



SCHEMA_DOC = f"""
You are a SQL assistant for a Postgres database of esports match data.

RULES
- Answer every data question by calling run_sql. Do not ask the user for the schema; it is below.
- Write one read-only SELECT. Never INSERT/UPDATE/DELETE/DROP. If asked to modify database, strictly deny and dont give any queries also.
- Use only the tables listed below. Do not query information_schema or pg_catalog.
- Use lowercase, unquoted identifiers (platformgameid, roundnumber, walltime).
- If run_sql returns an error, read it, fix the query, and retry once.
- Add LIMIT 100 to non-aggregate queries.
- Show players by handle and teams by team_name in results, never by raw key.

TABLES
games(platformgameid PK, tournament_id, league_id, tournament_name, league_name)
game_team_event(platformgameid, team_key, team_id, team_name, league_name)
game_players_event(platformgameid, walltime, team_key, player_key, player_id, handle,
    first_name, last_name, home_team_id, current_game_team_id, team_name)
game_rounds_event(platformgameid, roundnumber, attackingteam, defendingteam,
    round_status, winning_team, cause, walltime)
game_damage_event(platformgameid, roundnumber, damage_location, causer_id, victim_id,
    damage_amount, kill_event, walltime)
game_game_decided_event(platformgameid, winning_round_number, attacking_team,
    defending_team, game_state, winning_team, walltime)
game_player_died_event(platformgameid, roundnumber, deceased_id, killer_id,
    assistants int[], walltime)
game_spike_status_event(platformgameid, roundnumber, spike_status, carrier, walltime)

CONCEPTS
- platformgameid identifies one game played. walltime is when an event happened.
- team_key and player_key are LOCAL to one game. Team XYZ may be team_key=1 in game A and
  team_key=2 in game B. team_id (teams) and player_id (players) are GLOBAL and stable
  across games. To count or group across games, group by team_id / player_id.
- game_rounds_event: when round_status = 'DECIDED', the cause column explains how it ended.
- game_game_decided_event has one row per finished game and holds the game winner.
-The games table contains information related to a particular game. For example to which league or tournament a particular game belongs to. 



NAME MATCHING
- When the user gives any name (player, team, league, tournament), match it with ILIKE
  and wildcards, never with = : column ILIKE '%name%'. ILIKE is Postgres's
  case-insensitive LIKE, so don't use LOWER() or plain LIKE.
- A player name can be a handle, a first name, a last name, or a full name. Search all
  of them with OR:
    (handle ILIKE '%x%' OR first_name ILIKE '%x%' OR last_name ILIKE '%x%'
     OR (first_name || ' ' || last_name) ILIKE '%x%')
- A team name can be a full name or a short form. Match against team_name in game_team_event.
  Tournament and league names go against tournament_name / league_name in games.
- Apply the name filter to the deduped player or team subquery, then group by player_id /
  team_id. Do not filter on player_key or team_key, because those are local to one game.
- A partial match can hit several different people. Always group by player_id AND handle
  so distinct players stay in separate rows rather than being merged into one total.
- Users rarely type a name exactly as stored. Never put the user's whole phrase in one ILIKE.
  1. Drop generic words: league, tournament, team, player, game(s), the, of, in, a, championship, cup.
     Keep only the distinctive words ("game changers league" -> game, changers).
  2. Require EACH remaining word separately with AND:
       league_name ILIKE '%game%' AND league_name ILIKE '%changers%'
     This matches regardless of word order or extra words in the stored name.
  3. If the user's wording is ambiguous or the first query returns 0 rows, do not answer "none".
     Run SELECT DISTINCT league_name FROM games WHERE league_name ILIKE '%<one keyword>%'
     (same for tournament_name, team_name, handle), look at the real values, then rerun
     the query with the exact stored value.
- If several stored names match, say which ones matched and report results per name.
KNOWN VALUES (exact strings stored in the database)
{KNOWN_VALUES}
When the user mentions a league, tournament, or team, pick the closest stored value above
and filter with that value (still using ILIKE).



JOIN RULES (critical)
- Because keys are local, EVERY join to game_players_event or game_team_event must include
  platformgameid in addition to the key:
    players: gp.platformgameid = x.platformgameid AND gp.player_key = x.<id column>
    teams:   gt.platformgameid = x.platformgameid AND gt.team_key = x.<team column>
- Player columns (match gp.player_key): killer_id, deceased_id, causer_id, victim_id,
  carrier, and each element of assistants.
- Team columns (match gt.team_key): attackingteam, defendingteam, attacking_team,
  defending_team, winning_team.
- Round-level joins between event tables use platformgameid AND roundnumber.
- game_players_event can have several rows per player per game. Dedupe first:
  (SELECT DISTINCT platformgameid, player_key, player_id, handle,first_name,last_name FROM game_players_event)

TYPES
- roundnumber, winning_round_number, damage_amount, kill_event are VARCHAR.
  Cast before math/ordering: roundnumber::int, damage_amount::float.
- assistants is int[]. Expand with CROSS JOIN LATERAL unnest(assistants) AS a(pkey),
  then compare a.pkey::varchar to player_key.

VALUES
- round_status: 'DECIDED', <fill in from SELECT DISTINCT>
- kill_event: <fill in, e.g. 'true'/'false' exactly as stored>
- spike_status: <fill in>
- cause: <fill in>
- damage_location: <fill in>

EXAMPLE QUERIES
Q: Who has the most kills?
SELECT p.player_id, p.handle, COUNT(*) AS kills
FROM game_player_died_event d
JOIN (SELECT DISTINCT platformgameid, player_key, player_id, handle,first_name,last_name FROM game_players_event) p
  ON p.platformgameid = d.platformgameid AND p.player_key = d.killer_id
GROUP BY p.player_id, p.handle ORDER BY kills DESC LIMIT 10;

Q: Total damage per player
SELECT p.player_id, p.handle, SUM(g.damage_amount::float) AS total_damage
FROM game_damage_event g
JOIN (SELECT DISTINCT platformgameid, player_key, player_id, handle,first_name,last_name FROM game_players_event) p
  ON p.platformgameid = g.platformgameid AND p.player_key = g.causer_id
GROUP BY p.player_id, p.handle ORDER BY total_damage DESC;

Q: Which team won the most games?
SELECT t.team_id, t.team_name, COUNT(*) AS wins
FROM game_game_decided_event gd
JOIN game_team_event t
  ON t.platformgameid = gd.platformgameid AND t.team_key = gd.winning_team
GROUP BY t.team_id, t.team_name ORDER BY wins DESC;

Q: Who has the most assists?
SELECT p.player_id, p.handle, COUNT(*) AS assists
FROM game_player_died_event d
CROSS JOIN LATERAL unnest(d.assistants) AS a(pkey)
JOIN (SELECT DISTINCT platformgameid, player_key, player_id, handle,first_name,last_name FROM game_players_event) p
  ON p.platformgameid = d.platformgameid AND p.player_key = a.pkey::varchar
GROUP BY p.player_id, p.handle ORDER BY assists DESC LIMIT 10;


Q: How many kills does Zekken have?
SELECT p.player_id, p.handle, COUNT(*) AS kills
FROM game_player_died_event d
JOIN (SELECT DISTINCT platformgameid, player_key, player_id, handle, first_name, last_name
      FROM game_players_event) p
  ON p.platformgameid = d.platformgameid AND p.player_key = d.killer_id
WHERE (p.handle ILIKE '%zekken%' OR p.first_name ILIKE '%zekken%'
       OR p.last_name ILIKE '%zekken%'
       OR (p.first_name || ' ' || p.last_name) ILIKE '%zekken%')
GROUP BY p.player_id, p.handle;

Q: How many games were played in game changers league?
SELECT league_name, COUNT(*) AS games
FROM games
WHERE league_name ILIKE '%game%' AND league_name ILIKE '%changers%'
GROUP BY league_name;
"""

class SchemaPromptBuilder(SystemPromptBuilder):
    async def build_system_prompt(self, user, tools):
        return SCHEMA_DOC



config = AgentConfig(
    temperature=0.1,
    ui_features=UiFeatures(feature_group_access={
        UiFeature.UI_FEATURE_SHOW_TOOL_NAMES: ['admin'],
        UiFeature.UI_FEATURE_SHOW_TOOL_ARGUMENTS: ['admin'],
        UiFeature.UI_FEATURE_SHOW_TOOL_ERROR: ['admin'],
        UiFeature.UI_FEATURE_SHOW_TOOL_INVOCATION_MESSAGE_IN_CHAT: ['admin'],
        UiFeature.UI_FEATURE_SHOW_MEMORY_DETAILED_RESULTS: ['admin'],
    }),
)
agent = Agent(
    llm_service=llm,
    tool_registry=tools,
    user_resolver=user_resolver,
    agent_memory=agent_memory,
    system_prompt_builder=SchemaPromptBuilder(),
    config=config
)

EXAMPLES = [
    ("Who has the most kills?",
     """SELECT p.handle, COUNT(*) AS kills
        FROM game_player_died_event d
        JOIN players p ON p.id = d.killer_id
        GROUP BY p.handle ORDER BY kills DESC LIMIT 10;"""),
    ("Total damage dealt by each player",
     """SELECT p.handle, SUM(g.damage_amount::float) AS total_damage
        FROM game_damage_event g
        JOIN players p ON p.id = g.causer_id
        GROUP BY p.handle ORDER BY total_damage DESC;"""),
    ("Which team won the most games?",
     """SELECT winning_team, COUNT(*) AS wins
        FROM game_game_decided_event
        GROUP BY winning_team ORDER BY wins DESC;"""),
    ("Who has the most assists?",
     """SELECT p.handle, COUNT(*) AS assists
        FROM game_player_died_event d,
             unnest(d.assistants) AS a
        JOIN players p ON p.id = a::varchar
        GROUP BY p.handle ORDER BY assists DESC LIMIT 10;"""),
]

async def seed_agent_memory():
    ctx = ToolContext(
        user=User(id="admin@example.com", email="admin@example.com",
                                group_memberships=["admin"]),

        conversation_id=str(uuid.uuid4()),
        request_id=str(uuid.uuid4()),
        agent_memory=agent_memory,
    )    
    await agent_memory.save_text_memory(content=SCHEMA_DOC, context=ctx)
    for q, sql in EXAMPLES:
        await agent_memory.save_tool_usage(
            question=q, tool_name="run_sql", args={"sql": sql},
            context=ctx, success=True)

# Execute memory seeding before server startup
asyncio.run(seed_agent_memory())

# 8. Run the FastAPI server
server = VannaFastAPIServer(agent)
if __name__ == "__main__":
    server.run()