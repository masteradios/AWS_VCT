import csv
from pathlib import Path
import numpy as np
import pandas as pd
import json
import os

leagues = ['game-changers']
YEAR = 2023

def format_to_pg_array(val):
    if pd.isna(val) or val is None or val == "" or val == "[]":
        return "{}"

    # If it's a Python list object (e.g., ['4', '5'])
    if isinstance(val, list):
        return f"{{{','.join(map(str, val))}}}"

    # If it's a string representation of a list (e.g., "['4', '5']")
    s = str(val).strip()
    if s.startswith("[") and s.endswith("]"):
        inner = s[1:-1].replace("'", "").replace('"', "").replace(" ", "")
        return f"{{{inner}}}"

    return s

def map_round_numbers(
    event_df: pd.DataFrame,
    rounds_summary: pd.DataFrame,
    game_id_col: str = "platformGameId",
    time_col: str = "wallTime",
) -> pd.DataFrame:
    """Enriches any event DataFrame with roundNumber matched from rounds_summary."""
    if event_df.empty:
        return event_df

    # 1. Store original column order
    original_cols = event_df.columns.tolist()

    # Ensure 'roundNumber' exists in original_cols list
    if "roundNumber" not in original_cols:
        original_cols.append("roundNumber")

    # 2. Sort DataFrames by timestamp (required for merge_asof)
    event_sorted = event_df.sort_values(time_col)
    rounds_sorted = rounds_summary.sort_values("min_wall_time")

    # 3. Perform backward ASOF merge
    merged = pd.merge_asof(
        event_sorted,
        rounds_sorted,
        left_on=time_col,
        right_on="min_wall_time",
        left_by=game_id_col,
        right_by="platformGameId",
        direction="backward",
    )

    # 4. Vectorized sanity check: ensure wallTime <= max_wall_time
    matched_round_col = (
        "roundNumber_y" if "roundNumber_y" in merged.columns else "roundNumber"
    )

    merged["roundNumber"] = np.where(
        merged[time_col] <= merged["max_wall_time"],
        merged[matched_round_col],
        np.nan,
    )

    # 5. Return updated DataFrame sliced back to original columns
    return merged[original_cols]


for league in leagues:
    gamesDir = f"/home/adityak/projects/aws-hackathon/{league}/games/{YEAR}"
    gamesDirectory = Path(gamesDir)
    
    leagueDir = f"/home/adityak/projects/aws-hackathon/results/league={league}"
    textDir = f"/home/adityak/projects/aws-hackathon/results/text/league={league}/year={YEAR}"

    for file_path in gamesDirectory.glob('val:*.json'):
        if not file_path.is_file():
            continue

        print(f"File Name: {file_path.name}")
        game_id_clean = file_path.name.replace('.json', '')

        # Base directory for raw text files belonging to this game
        source_text_dir = f"/home/adityak/projects/aws-hackathon/results/text/league={league}/year={YEAR}/"

        # Load enriched lookup data
        players = pd.read_csv(f'{leagueDir}/players_enriched.csv', dtype={"home_team_id": "str"}).astype(str)
        teams = pd.read_csv(f'{leagueDir}/teams_enriched.csv').astype(str)
        configuration = pd.read_csv(f'{source_text_dir}/configuration/{game_id_clean}:configuration.txt').astype(str)
    
        # Process and save players configuration mapping
        game_wise_players_config = (
            pd.merge(
                configuration,
                players,
                how='left',
                left_on=['platformGameId', 'player_key'],
                right_on=['platformGameId', 'playerKey']
            )
            .merge(
                teams,
                how='left',
                left_on=['platformGameId', 'team_key'],
                right_on=['platformGameId', 'teamKey']
            )
            .drop(columns=['playerKey', 'league_x', 'teamKey', 'league_y'], errors='ignore')
            .rename(columns={"teamId": "current_game_team_id", "playerId": "player_id"})
        )
        game_wise_players_config.to_csv(f'{textDir}/players.csv', index=False)
        
        # Read and merge rounds started & decided into roundsEventUpdated
        rounds_started = pd.read_csv(f'{source_text_dir}/rounds_started/{game_id_clean}:rounds_started.txt').astype(str)
        rounds_ended = pd.read_csv(f'{source_text_dir}/rounds_decided/{game_id_clean}:rounds_decided.txt').astype(str)

        rounds = pd.concat([rounds_started, rounds_ended], ignore_index=True)
        
        # Write merged rounds to dedicated updated folder inside textDir
        rounds_output_dir = f"{textDir}/roundsEventUpdated"
        os.makedirs(rounds_output_dir, exist_ok=True)
        rounds.to_csv(f"{rounds_output_dir}/{game_id_clean}:roundsEventUpdated.txt", index=False)

        rounds['wallTime'] = pd.to_datetime(rounds['wallTime'], format='ISO8601')
        rounds['roundNumber'] = pd.to_numeric(rounds['roundNumber'], errors='coerce')
        
        rounds_sorted = (
            rounds
            .groupby(['platformGameId', 'roundNumber'])['wallTime']
            .agg(min_wall_time='min', max_wall_time='max')
            .reset_index()
            .sort_values(by=["platformGameId", "roundNumber"])
        )
        rounds_summary = rounds_sorted.sort_values('min_wall_time')

        # Process Damage Event Update
        damageEvent = pd.read_csv(f'{source_text_dir}/damageEvent/{game_id_clean}:damageEvent.txt').astype(str)
        damageEvent['wallTime'] = pd.to_datetime(damageEvent['wallTime'], format='ISO8601')
        damageEvent['roundNumber'] = pd.to_numeric(damageEvent['roundNumber'], errors='coerce')
        damageEvent = damageEvent.sort_values('wallTime')
        damageEvent = map_round_numbers(
            damageEvent, rounds_summary, game_id_col="platformGameId"
        )
        
        damage_output_dir = f"{textDir}/damageEvent_updated"
        os.makedirs(damage_output_dir, exist_ok=True)
        damageEvent.to_csv(f"{damage_output_dir}/{game_id_clean}:damageEvent_updated.txt", index=False)

        # Process Player Died Update
        playerDied = pd.read_csv(f'{source_text_dir}/playerDied/{game_id_clean}:playerDied.txt', parse_dates=["wallTime"])
        playerDied['wallTime'] = pd.to_datetime(playerDied['wallTime'], format='ISO8601')
        playerDied['roundNumber'] = pd.to_numeric(playerDied['roundNumber'], errors='coerce')
        playerDied = playerDied.sort_values('wallTime')
        playerDied = map_round_numbers(
            playerDied, rounds_summary, game_id_col="platformGameId"
        )

        playerDied["assistants"] = playerDied["assistants"].apply(format_to_pg_array)
        
        died_output_dir = f"{textDir}/playerDied_updated"
        os.makedirs(died_output_dir, exist_ok=True)
        playerDied.to_csv(
            f"{died_output_dir}/{game_id_clean}:playerDied_updated.txt",
            index=False,
            quoting=csv.QUOTE_NONE,
            escapechar="\\",
        )

        # Process Spike Status Update
        spikeStatus = pd.read_csv(f'{source_text_dir}/spikeStatus/{game_id_clean}:spikeStatus.txt', parse_dates=["wallTime"])
        spikeStatus['wallTime'] = pd.to_datetime(spikeStatus['wallTime'], format='ISO8601')
        spikeStatus['roundNumber'] = pd.to_numeric(spikeStatus['roundNumber'], errors='coerce')
        spikeStatus = spikeStatus.sort_values('wallTime')
        spikeStatus = map_round_numbers(
            spikeStatus, rounds_summary, game_id_col="platformGameId"
        )
        
        spike_output_dir = f"{textDir}/spikeStatus_updated"
        os.makedirs(spike_output_dir, exist_ok=True)
        spikeStatus.to_csv(f"{spike_output_dir}/{game_id_clean}:spikeStatus_updated.txt", index=False)