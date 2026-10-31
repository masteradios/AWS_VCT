# AWS Based LLM Assistant for Esports (Valorant) Analytics

An AWS Bedrock–based agent that answers natural-language questions about VCT (Valorant Champions Tour) hackathon data. This repository holds the data pipeline behind it: it pulls VCT data from S3, splits each game's event stream into per-event tables, enriches them with team/player metadata, and publishes them as Glue / Lake Formation managed tables that the agent uses as its data source.

- **Dev scope:** league `game-changers`, years 2022,2023 and 2024
- **Bucket:** `aws-vct`
- **Dataset docs:** [VCT hackathon data guide](https://docs.google.com/document/d/19H3FsWYEli6ShIV5_5fmhq2xDdfcQOCXlRiNvKZLYL8/edit?tab=t.0#heading=h.lkyo8c40dcz1)

### Architecture Diagram :

<img width="1302" height="862" alt="AWS_VCT" src="https://github.com/user-attachments/assets/5efc575e-c4a0-4b32-8adc-5071f03e5e42" />


## Project Goal

Build an AWS Bedrock agent that lets a user ask questions about the VCT hackathon data in plain English and get answers grounded in the data. The Lake Formation–managed tables in the Glue database `aws_vct` (produced by this pipeline) are the agent's data source.

Example questions it should answer:
- Which player assisted the most?
- How long did round 1 last?
- Which team won the most rounds, and how (spike defuse, detonation, eliminations)?


<!-- ## Pipeline Overview

```
downloadData ──► extractMapping ──► enrichMapping          (esports metadata)
      │
      └──────► extractEventsJson ──► extractEventsText ──► producePlayerEvent   (game events)
``` -->

Lambdas are chained by EventBridge rules. `extractEventsJson` → `extractEventsText` → `producePlayerEvent` pass a `message` in the event detail (`Json Extracted : <key>`, then `Text Extracted : <key>`).

| # | Lambda | Purpose |
|---|--------|---------|
| 1 | `downloadData` | Download raw VCT data from the public S3 bucket |
| 2 | `extractMapping` | Flatten team/player/game mappings into CSVs |
| 3 | `enrichMapping` | Add team and player details to the mappings |
| 4 | `extractEventsJson` | Split a game file into one JSON file per event type |
| 5 | `extractEventsText`| Flatten event JSON into CSV-style text tables |
| 6 | `producePlayerEvent` | Enrich players, derive `roundNumber`, write `_updated` tables |

## 1. downloadData

Downloads gzipped JSON from `https://vcthackathon-data.s3.us-west-2.amazonaws.com`, decompresses in memory, and writes plain JSON to `aws-vct`. Existing objects are skipped.

- **Esports data** (`{league}/esports-data/`): `leagues`, `tournaments`, `players`, `teams`, `mapping_data`
- **Game files:** one file per game, each containing every event type for that game. Only **5 games per run** are downloaded because the files are large.

Output:
```
rawData/{league}/esports-data/{leagues|tournaments|players|teams|mapping_data}.json
rawData/{league}/games/{year}/val:{platformGameId}.json
```
Marker files: `rawData/{league}/esports-data/.success`, `rawData/{league}/games/{year}/.success.games`

**Trigger:** invoked directly.

**Publishes:** no events. It writes marker files (`.success`, `.success.games`). Each game file it writes into `rawData/` fires `game-arrival-rule`, which starts the event chain for that game.


## 2. extractMapping

Reads `leagues`, `tournaments`, `teams` and `mapping_data` and expands the nested mappings into rows.

Output (`intermediateData/{league}/esports-data/`):
- `teams.csv`: `platformGameId, teamKey, teamId` + team details
- `players.csv`: `platformGameId, playerKey, playerId`
- `games.csv`: `platformGameId, tournamentId, league_id, tournament_name, league_name`

Marker: `.success.extractMapping`

**Trigger:** invoked once `.success` file is written.

**Publishes:** no events. It writes the marker `.success.extractMapping`.

## 3. enrichMapping

Joins `teams.csv` / `players.csv` with the raw `teams.json` / `players.json`, adds a `league` column, then deletes the two source CSVs.

Output (`intermediateData/{league}/esports-data/`):
- `teams_enriched.csv`
- `players_enriched.csv`

Marker: `.success.enrichMapping`

**Trigger:** invoked once `.success.extractMapping` file is written.

**Publishes:** no events. It writes the marker `.success.enrichMapping`.



## 4. extractEventsJson

Triggered when a game file lands in `rawData/{league}/games/{year}/`. Every event has `platformGameId` + `metadata` plus one extra key, and that key is the event type. Events are grouped by it and only the required types are kept:

`configuration, damageEvent, gameDecided, gamePhase, playerDied, roundCeremony, roundDecided, roundStarted, snapshot, spikeStatus`

Output:
```
eventsData/json/league={league}/year={year}/val:{gameId}:{eventType}.json
```

**Trigger:** rule `game-arrival-rule` (source `aws.s3`, detail-type `Object Created`, bucket `aws-vct`, key `rawData/*/val*.json`).

**Publishes:** source `extract.events.json`, detail-type `JSON Events Extraction`, `detail.message` = `Json Extracted : <raw game file key>`, on the `default` bus.


## 5. extractEventsText

Flattens the event JSON into text tables (`wallTime` converted to Athena format `yyyy-MM-dd HH:mm:ss.SSS`, UTC).

Output: `eventsData/text/{table}/league={league}/year={year}/val:{gameId}:{name}.txt`


| Table folder | Columns |
|---|---|
| `configuration` | `platform_id, wallTime, team_key, player_key` |
| `rounds_started` | `platform_id, roundNumber, attackingTeam, defendingTeam, round_status (STARTED), winning_team, cause, wallTime` |
| `rounds_decided` | same columns, `round_status = DECIDED`, with `winning_team` and `cause` |
| `spikeStatus` | `platform_id, roundNumber, spike_status, carrier, wallTime` |
| `damageEvent` | `platform_id, roundNumber, damage_location, causer_id, victim_id, damage_amount, kill_event, wallTime` |
| `playerDied` | `platform_id, roundNumber, deceased_id, killer_id, assistants, wallTime` |
| `gameDecided` | `platform_id, winning_round_number, attacking_team, defending_team, game_state, winning_team, wallTime` |

`roundNumber` is blank for `spikeStatus`, `damageEvent` and `playerDied` (see below). `gamePhase`, `roundCeremony` and `snapshot` are kept as JSON only.

**Trigger:** rule `convert-event-json-to-text` (source `extract.events.json`, detail-type `JSON Events Extraction`, message `Json Extracted : *`).

**Publishes:** source `extract.events.text`, detail-type `Text Events Extraction`, `detail.message` = `Text Extracted : <raw game file key>`, on the `default` bus. It publishes only after the game was processed successfully.


## 6. producePlayerEvent

Loads `players_enriched.csv` / `teams_enriched.csv`, then produces the final tables under `eventsData/text/`:



| Output folder | Content |
|---|---|
| `game_players_event/` | `configuration` joined with player and team details (`player_id`, `current_game_team_id`, names) |
| `roundsEventUpdated/` | `rounds_started` + `rounds_decided` combined into one round event log |
| `damageEvent_updated/` | `damageEvent` with `roundNumber` filled |
| `playerDied_updated/` | `playerDied` with `roundNumber` filled; `assistants` written as `a\|b\|c` |
| `spikeStatus_updated/` | `spikeStatus` with `roundNumber` filled |

All paths follow `{folder}/league={league}/year={year}/val:{gameId}:{name}.txt`.

## How `roundNumber` is created for `_updated` events

`damageEvent`, `playerDied` and `spikeStatus` have **no round number** in their source JSON, so it is derived from time:

1. Combine `rounds_started` and `rounds_decided` (both carry `roundNumber`).
2. Group by `(platform_id, roundNumber)` and take `min(wallTime)` (round started) and `max(wallTime)` (round decided) to get each round's time window.
3. For every event, `pd.merge_asof` (backward, same `platformGameId`) finds the latest round whose start is at or before the event's `wallTime`.
4. The match is kept only if `wallTime <= max_wall_time` of that round; otherwise `roundNumber` is left empty (NULL).

Events that fall between a round's decision and the next round's start therefore get no round number.

**Trigger:** rule `produce-events` (source `extract.events.text`, detail-type `Text Events Extraction`, message `Text Extracted : *`).

**Publishes:** nothing. It is the last step in the chain.

## Lake Formation / Glue

Files written by `extractEventsText` and `producePlayerEvent` are managed by **Lake Formation** through the **Glue Data Catalog** (database `aws_vct`). Partitions are `league` and `year`, registered with Glue `create_partition` (existing partitions are skipped). The default SerDe is `OpenCSVSerde` (all columns as strings); `LazySimpleSerDe` with `|` as the collection delimiter is available for tables with `array<string>` columns.

Tables: `game_players_event`, `game_rounds_event`, `game_damage_event`, `game_player_died_event`, `game_spike_status_event`, `game_game_decided_event`.

## Glue Table Definitions

Athena DDL for the tables in database `aws_vct`, for reference. Files have a header row and are partitioned by `league` and `year`; partitions are registered by the Lambdas through the Glue API, so no `MSCK REPAIR TABLE` is needed.

Notes for querying:
- All columns are `string` (`OpenCSVSerde`). Cast at query time, e.g. `CAST(wallTime AS timestamp)`.
- In the `_updated` tables, `roundNumber` may be stored as `1.0`. Use `CAST(CAST(roundNumber AS double) AS integer)` before joining to `game_rounds_event`.

```sql




-- ============================================================================
-- 1. game_damage_event

-- ============================================================================
CREATE EXTERNAL TABLE aws_vct.game_damage_event (
  platformgameid string,
  roundnumber string,
  damage_location string,
  causer_id string,
  victim_id string,
  damage_amount string,
  kill_event string,
  walltime string
)
PARTITIONED BY (
  league string,
  year int
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://aws-vct/eventsData/text/damageEvent_updated/'
TBLPROPERTIES ('classification' = 'csv','skip.header.line.count'='1');


-- ============================================================================
-- 2. game_game_decided_event
-- ============================================================================
CREATE EXTERNAL TABLE aws_vct.game_game_decided_event (
  platformgameid string,
  winning_round_number string,
  attacking_team string,
  defending_team string,
  game_state string,
  winning_team string,
  walltime string
)
PARTITIONED BY (
  league string,
  year int
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://aws-vct/eventsData/text/gameDecided/'
TBLPROPERTIES ('classification' = 'csv', 'skip.header.line.count' = '1');


-- ============================================================================
-- 3. game_player_died_event

-- ============================================================================
CREATE EXTERNAL TABLE aws_vct.game_player_died_event (
  platformgameid string,
  roundnumber string,
  deceased_id string,
  killer_id string,
  assistants string,
  walltime string
)
PARTITIONED BY (
  league string,
  year int
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://aws-vct/eventsData/text/playerDied_updated/'
TBLPROPERTIES ('classification' = 'csv','skip.header.line.count'='1');


-- ============================================================================
-- 4. game_players_event
-- ============================================================================
CREATE EXTERNAL TABLE aws_vct.game_players_event (
  platformgameid string,
  walltime string,
  team_key string,
  player_key string,
  player_id string,
  handle string,
  first_name string,
  last_name string,
  home_team_id string,
  current_game_team_id string,
  team_name string
)
PARTITIONED BY (
  league string,
  year int
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://aws-vct/eventsData/text/game_players_event/'
TBLPROPERTIES ('classification' = 'csv','skip.header.line.count'='1');


-- ============================================================================
-- 5. game_rounds_event
-- ============================================================================
CREATE EXTERNAL TABLE aws_vct.game_rounds_event (
  platformgameid string,
  roundnumber string,
  attackingteam string,
  defendingteam string,
  round_status string,
  winning_team string,
  cause string,
  walltime string
)
PARTITIONED BY (
  league string,
  year int
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://aws-vct/eventsData/text/roundsEventUpdated/'
TBLPROPERTIES ('classification' = 'csv','skip.header.line.count'='1');


-- ============================================================================
-- 6. game_spike_status_event
-- ============================================================================
CREATE EXTERNAL TABLE aws_vct.game_spike_status_event (
  platformgameid string,
  roundnumber string,
  spike_status string,
  carrier string,
  walltime string
)
PARTITIONED BY (
  league string,
  year int
)
ROW FORMAT SERDE 'org.apache.hadoop.hive.serde2.OpenCSVSerde'
WITH SERDEPROPERTIES ('separatorChar' = ',')
STORED AS INPUTFORMAT 'org.apache.hadoop.mapred.TextInputFormat'
OUTPUTFORMAT 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat'
LOCATION 's3://aws-vct/eventsData/text/spikeStatus_updated/'
TBLPROPERTIES ('classification' = 'csv','skip.header.line.count'='1');
```



## IAM Roles

| # | Role | Used by | Allows |
|---|---|---|---|
| 1 | EventBridge → Lambda | Amazon EventBridge | Invoke `extractEventsJson`, `extractEventsText`, `producePlayerEvent` |
| 2 | Lambda execution | Pipeline Lambdas | CloudWatch Logs, S3 on `aws-vct`, EventBridge `PutEvents` on the default bus, Glue catalog (create partitions, read tables) |
| 3 | EC2 → Bedrock + Athena | Streamlit app on EC2 | Invoke Bedrock models, run Athena queries in `primary`, read/write Athena results in `s3://aws-vct/athenaLogs/` |

#### Permission details for each Role : 

<details>
<summary>1. Role for Amazon EventBridge to invoke Lambdas</summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["lambda:InvokeFunction"],
      "Resource": [
        "arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:extractEventsJson",
        "arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:extractEventsText",
        "arn:aws:lambda:us-east-1:<ACCOUNT_ID>:function:producePlayerEvent"
      ]
    }
  ]
}
```
</details>

<details>
<summary>2. Role for Lambda to interact with S3, EventBridge and Glue</summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CloudWatchLogsPermissions",
      "Effect": "Allow",
      "Action": ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "*"
    },
    {
      "Sid": "S3BucketPermissions",
      "Effect": "Allow",
      "Action": [
        "s3:GetObjectAttributes", "s3:ListBucket", "s3:ListBucketMultipartUploads",
        "s3:DeleteObject", "s3:GetObject", "s3:PutObject"
      ],
      "Resource": ["arn:aws:s3:::aws-vct", "arn:aws:s3:::aws-vct/*"]
    },
    {
      "Sid": "EventBridgePermissions",
      "Effect": "Allow",
      "Action": [
        "events:ActivateEventSource", "events:AllowVendedLogDeliveryForResource",
        "events:CancelReplay", "events:CreateApiDestination", "events:CreateArchive",
        "events:CreateConnection", "events:CreateEndpoint", "events:CreateEventBus",
        "events:CreateEventSource", "events:CreatePartnerEventSource", "events:CreateSubscriber",
        "events:DeactivateEventSource", "events:DeauthorizeConnection",
        "events:DescribeApiDestination", "events:DescribeArchive", "events:DescribeConnection",
        "events:DescribeEndpoint", "events:DescribeEventBus", "events:DescribeEventSource",
        "events:DescribePartnerEventSource", "events:DescribeReplay", "events:DescribeRule",
        "events:DescribeSubscriber", "events:GetResourcePolicy", "events:PutEvents",
        "events:TestEventPattern"
      ],
      "Resource": ["arn:aws:events:us-east-1:<ACCOUNT_ID>:event-bus/default"]
    },
    {
      "Sid": "GlueCatalogPermissions",
      "Effect": "Allow",
      "Action": [
        "glue:BatchCreatePartition", "glue:BatchDeletePartition", "glue:CreateDatabase",
        "glue:CreatePartition", "glue:CreateTable", "glue:DeleteTable", "glue:GetDatabase",
        "glue:GetDatabases", "glue:GetPartition", "glue:GetPartitions", "glue:GetTable",
        "glue:GetTables", "glue:UpdateTable"
      ],
      "Resource": [
        "arn:aws:glue:us-east-1:<ACCOUNT_ID>:catalog",
        "arn:aws:glue:us-east-1:<ACCOUNT_ID>:database/aws_vct*",
        "arn:aws:glue:us-east-1:<ACCOUNT_ID>:table/aws_vct/*"
      ]
    }
  ]
}
```
</details>

<details>
<summary>3. Role for EC2 to interact with Bedrock and Athena</summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "BedrockInvoke",
      "Effect": "Allow",
      "Action": ["bedrock:InvokeModel", "bedrock:Converse", "bedrock:InvokeModelWithResponseStream"],
      "Resource": "*"
    },
    {
      "Sid": "AthenaQuery",
      "Effect": "Allow",
      "Action": [
        "athena:StartQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults",
        "athena:StopQueryExecution", "athena:GetWorkGroup"
      ],
      "Resource": "arn:aws:athena:us-east-1:<ACCOUNT_ID>:workgroup/primary"
    },
    {
      "Sid": "S3AthenaResults",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:GetBucketLocation",
        "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"
      ],
      "Resource": ["arn:aws:s3:::aws-vct", "arn:aws:s3:::aws-vct/athenaLogs/*"]
    }
  ]
}
```
</details>

## Pipeline Overview  (replace the existing diagram and the "chained by EventBridge" sentence)

```
downloadData ──► extractMapping ──► enrichMapping        (not triggered by EventBridge)

game file lands in S3: rawData/{league}/games/{year}/val:{gameId}.json
   │  rule: game-arrival-rule
   ▼
extractEventsJson
   │  rule: convert-event-json-to-text
   ▼
extractEventsText
   │  rule: produce-events
   ▼
producePlayerEvent
```

Only the game-event Lambdas (`extractEventsJson`, `extractEventsText`, `producePlayerEvent`) are triggered by EventBridge. Each game file that arrives starts its own run of the chain.

## EventBridge Rules

All rules are on the `default` event bus in `us-east-1`.

| Rule | Fires on | Target |
|---|---|---|
| `game-arrival-rule` | S3 `Object Created` in bucket `aws-vct`, key matching `rawData/*/val*.json` (a game file arriving) | `extractEventsJson` |
| `convert-event-json-to-text` | Custom event from `extractEventsJson` (`Json Extracted : *`) | `extractEventsText` |
| `produce-events` | Custom event from `extractEventsText` (`Text Extracted : *`) | `producePlayerEvent` |

**Hand-off contract:** the custom events carry the key of the raw game file in `detail.message`, for example `Json Extracted : rawData/game-changers/games/2022/val:<gameId>.json`. Each Lambda parses the league, year and game ID from that key, so the key layout (`rawData/{league}/games/{year}/val:{gameId}.json`) must stay as is.

<details>
<summary>Event patterns</summary>

`game-arrival-rule` → `extractEventsJson`
```json
{
  "source": ["aws.s3"],
  "detail-type": ["Object Created"],
  "detail": {
    "bucket": { "name": ["aws-vct"] },
    "object": { "key": [{ "wildcard": "rawData/*/val*.json" }] }
  }
}
```

`convert-event-json-to-text` → `extractEventsText`
```json
{
  "source": ["extract.events.json"],
  "detail-type": ["JSON Events Extraction"],
  "detail": { "message": [{ "wildcard": "Json Extracted : *" }] }
}
```

`produce-events` → `producePlayerEvent`
```json
{
  "source": ["extract.events.text"],
  "detail-type": ["Text Events Extraction"],
  "detail": { "message": [{ "wildcard": "Text Extracted : *" }] }
}
```
</details>

**Notes**
- The `aws-vct` bucket must have **Amazon EventBridge notifications** turned on, or `game-arrival-rule` never fires.
- The pattern only matches raw game files. The pipeline's own outputs (`eventsData/`, `intermediateData/`) and the `.success` marker files do not match, so the chain cannot trigger itself.
- **Order matters.** `producePlayerEvent` needs `players_enriched.csv` and `teams_enriched.csv`, so run `extractMapping` and then `enrichMapping` before game files arrive. If those files are missing, it returns 500 and writes nothing, and nothing retriggers it. To reprocess a game, re-put its raw file in `rawData/` so `game-arrival-rule` fires again.
## How the Agent Runs

The agent is a **Streamlit app (`app.py`) deployed on an EC2 instance**. It takes a question in chat, has an Amazon Bedrock model write Athena SQL, runs that SQL against the Glue/Lake Formation tables, and answers from the result.

```
User (Streamlit chat, EC2)
   │ question
   ▼
Bedrock Converse API (tool use, tool = run_sql)
   │ SQL
   ▼
validate_sql (read-only + Athena dialect checks)
   │ valid SQL
   ▼
Athena (workgroup: primary) ──► Glue Data Catalog / Lake Formation tables (aws_vct)
   │ rows (first 50 sent to the model)
   ▼
Bedrock writes a short answer ──► UI shows answer + result table (+ chart)
```

Run: `streamlit run app.py`

**Configuration** (environment variables or `.env`):

| Variable | Default |
|---|---|
| `AWS_REGION` | `us-east-1` |
| `GLUE_DATABASE` | `aws_vct` |
| `ATHENA_WORKGROUP` | `primary` |
| `ATHENA_OUTPUT_S3` | `s3://aws-vct/athenaLogs/` |
| `BEDROCK_MODEL_ID` | `amazon.nova-pro-v1:0` |
| `ADMIN_EMAIL` | `admin@example.com` |

**Agent loop**
- Uses the Bedrock Converse API with a single tool, `run_sql` (temperature 0.1, max 2048 tokens).
- The model gets up to 6 SQL attempts per question. SQL errors are returned to the model so it can fix the query and retry.
- Only the first 50 rows of a result are sent back to the model; the UI shows the full result.
- Chat history is passed on each turn, so follow-up questions work.

**System prompt (built at runtime)**
- The live table schema, read from the Glue Data Catalog with `get_tables` (cached for 1 hour).
- Known league, tournament and team names, queried from Athena (cached for 1 hour), so the model can match names users type loosely.
- Athena/Trino dialect rules, data concepts, join rules and example queries.

**Data conventions the prompt teaches the model**
- `team_key` and `player_key` are local to one game. Use `team_id` and `player_id` to group across games, and always join on `platformgameid` plus the key.
- `walltime` is a string, so cast it with `CAST(walltime AS timestamp)`.
- `assistants` is a `|`-delimited string; the prompt shows how to split it into an array.
- `roundnumber` is text (`'3'`), so cast it with `TRY(CAST(CAST(roundnumber AS double) AS integer))`.
- Names are matched with `lower(col) LIKE '%x%'`, not `=`.

**Tables the agent can query:**  `game_team_event`, `game_players_event`, `game_rounds_event`, `game_damage_event`, `game_game_decided_event`, `game_player_died_event`, `game_spike_status_event`

**Guardrails**
- `validate_sql` accepts a single read-only `SELECT`/`WITH` statement, with no comments, DDL/DML keywords or system catalogs.
- It also rejects syntax that is not valid in Athena (for example `ILIKE`, `::`, `TOP`, `datediff`), with a message telling the model how to fix it.
- Queries run with `ctas_approach=False`, so no temporary tables are created.
- The prompt tells the model to refuse any request to change data.
- The EC2 role (below) only allows querying, with no write access to the data.



