import csv
import json
import os
from pathlib import Path

leagues = ['game-changers']
YEAR = 2023

for league in leagues:
    gamesDir = f"/home/adityak/projects/aws-hackathon/{league}/games/{YEAR}"
    gamesDirectory = Path(gamesDir)

    for file_path in gamesDirectory.glob('val:*.json'):
        if not file_path.is_file():
            continue

        print(f"File Name: {file_path.name}")
        
        # Correctly extract the game ID string
        game_id_clean = file_path.name.replace('.json', '')

        jsonDir = f"/home/adityak/projects/aws-hackathon/results/json/league={league}/year={YEAR}"
        textDir = f"/home/adityak/projects/aws-hackathon/results/text/league={league}/year={YEAR}"

        # 1. Configuration Table
        config_json_path = f"{jsonDir}/configuration/{game_id_clean}:configuration.json"
        if os.path.exists(config_json_path):
            with open(config_json_path, 'r', encoding="utf-8") as f:
                data = json.load(f)
            
            if data:
                event = data[0]
                platformGameId = event['platformGameId']
                wallTime = event['metadata']['wallTime']
                
                config_text_dir = f"{textDir}/configuration"
                os.makedirs(config_text_dir, exist_ok=True)
                
                with open(f"{config_text_dir}/{game_id_clean}:configuration.txt", "w", encoding="utf-8") as f:
                    f.write("platformGameId,wallTime,team_key,player_key\n")
                    for team in event['configuration']['teams']:
                        team_id = team['teamId']['value']
                        players = [p["value"] for p in team["playersInTeam"]]
                        for player in players:
                            f.write(f"{platformGameId},{wallTime},{team_id},{player}\n")

        # 2. Rounds Started Table
        started_json_path = f"{jsonDir}/roundStarted/{game_id_clean}:roundStarted.json"
        if os.path.exists(started_json_path):
            with open(started_json_path, 'r', encoding="utf-8") as f:
                events = json.load(f)
            
            if events:
                platformGameId = events[0]['platformGameId']
                started_text_dir = f"{textDir}/rounds_started"
                os.makedirs(started_text_dir, exist_ok=True)
                
                with open(f"{started_text_dir}/{game_id_clean}:rounds_started.txt", "w", encoding="utf-8") as f:
                    f.write("platformGameId,roundNumber,attackingTeam,defendingTeam,round_status,winning_team,cause,wallTime\n")
                    for event in events:
                        roundStarted = event['roundStarted']
                        roundNumber = roundStarted['roundNumber']
                        attackingTeam = roundStarted['spikeMode']['attackingTeam']['value']
                        defendingTeam = roundStarted['spikeMode']['defendingTeam']['value']
                        round_status = "STARTED"
                        winning_team = ""
                        cause = ""
                        wallTime = event['metadata']['wallTime']
                        f.write(f"{platformGameId},{roundNumber},{attackingTeam},{defendingTeam},{round_status},{winning_team},{cause},{wallTime}\n")

        # 3. Rounds Decided Table
        decided_json_path = f"{jsonDir}/roundDecided/{game_id_clean}:roundDecided.json"
        if os.path.exists(decided_json_path):
            with open(decided_json_path, 'r', encoding="utf-8") as f:
                events = json.load(f)
            
            if events:
                platformGameId = events[0]['platformGameId']
                decided_text_dir = f"{textDir}/rounds_decided"
                os.makedirs(decided_text_dir, exist_ok=True)
                
                with open(f"{decided_text_dir}/{game_id_clean}:rounds_decided.txt", "w", encoding="utf-8") as f:
                    f.write("platformGameId,roundNumber,attackingTeam,defendingTeam,round_status,winning_team,cause,wallTime\n")
                    for event in events:
                        result = event['roundDecided']['result']
                        roundNumber = result['roundNumber']
                        attackingTeam = result['spikeModeResult']['attackingTeam']['value']
                        defendingTeam = result['spikeModeResult']['defendingTeam']['value']
                        round_status = "DECIDED"
                        winning_team = result['winningTeam']['value']
                        cause = result['spikeModeResult']['cause']
                        wallTime = event['metadata']['wallTime']
                        f.write(f"{platformGameId},{roundNumber},{attackingTeam},{defendingTeam},{round_status},{winning_team},{cause},{wallTime}\n")

        # 4. Spike Status Table
        spike_json_path = f"{jsonDir}/spikeStatus/{game_id_clean}:spikeStatus.json"
        if os.path.exists(spike_json_path):
            with open(spike_json_path, 'r', encoding="utf-8") as f:
                events = json.load(f)
            
            if events:
                platformGameId = events[0]['platformGameId']
                spike_text_dir = f"{textDir}/spikeStatus"
                os.makedirs(spike_text_dir, exist_ok=True)
                
                with open(f"{spike_text_dir}/{game_id_clean}:spikeStatus.txt", "w", encoding="utf-8") as f:
                    f.write("platformGameId,roundNumber,spike_status,carrier,wallTime\n")
                    for event in events:
                        spikeStatus = event['spikeStatus']['status']
                        roundNumber = ""
                        carrier = event['spikeStatus']['carrier']['value'] if spikeStatus == "IN_HANDS" else ""
                        wallTime = event['metadata']['wallTime']
                        f.write(f"{platformGameId},{roundNumber},{spikeStatus},{carrier},{wallTime}\n")

        # 5. Damage Event Table
        damage_json_path = f"{jsonDir}/damageEvent/{game_id_clean}:damageEvent.json"
        if os.path.exists(damage_json_path):
            with open(damage_json_path, 'r', encoding="utf-8") as f:
                events = json.load(f)
            
            if events:
                platformGameId = events[0]['platformGameId']
                damage_text_dir = f"{textDir}/damageEvent"
                os.makedirs(damage_text_dir, exist_ok=True)
                
                with open(f"{damage_text_dir}/{game_id_clean}:damageEvent.txt", "w", encoding="utf-8") as f:
                    f.write("platformGameId,roundNumber,damage_location,causer_id,victim_id,damage_amount,kill_event,wallTime\n")
                    for event in events:
                        damageEvent = event['damageEvent']
                        damage_location = damageEvent['location']
                        roundNumber = ""
                        causer_id = damageEvent['causerId']['value']
                        victim_id = damageEvent['victimId']['value']
                        damage_amount = damageEvent['damageAmount']
                        kill_event = damageEvent['killEvent']
                        wallTime = event['metadata']['wallTime']
                        f.write(f"{platformGameId},{roundNumber},{damage_location},{causer_id},{victim_id},{damage_amount},{kill_event},{wallTime}\n")

        # 6. Player Died Table
        died_json_path = f"{jsonDir}/playerDied/{game_id_clean}:playerDied.json"
        if os.path.exists(died_json_path):
            with open(died_json_path, 'r', encoding="utf-8") as f:
                events = json.load(f)
            
            if events:
                platformGameId = events[0]['platformGameId']
                died_text_dir = f"{textDir}/playerDied"
                os.makedirs(died_text_dir, exist_ok=True)
                
                with open(f"{died_text_dir}/{game_id_clean}:playerDied.txt", "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
                    writer.writerow(["platformGameId", "roundNumber", "deceased_id", "killer_id", "assistants", "wallTime"])

                    for event in events:
                        playerDied = event['playerDied']
                        deceased_id = playerDied['deceasedId']['value']
                        roundNumber = ""
                        killer_id = playerDied['killerId']['value']
                        assistants = [p["assistantId"]['value'] for p in playerDied["assistants"]]
                        wallTime = event['metadata']['wallTime']

                        writer.writerow([platformGameId, roundNumber, deceased_id, killer_id, assistants, wallTime])

        # 7. Game Decided Table
        game_decided_json_path = f"{jsonDir}/gameDecided/{game_id_clean}:gameDecided.json"
        if os.path.exists(game_decided_json_path):
            with open(game_decided_json_path, 'r', encoding="utf-8") as f:
                events = json.load(f)
            
            if events:
                platformGameId = events[0]['platformGameId']
                game_decided_text_dir = f"{textDir}/gameDecided"
                os.makedirs(game_decided_text_dir, exist_ok=True)
                
                with open(f"{game_decided_text_dir}/{game_id_clean}:gameDecided.txt", "w", encoding="utf-8") as f:
                    f.write("platformGameId,winning_round_number,attacking_team,defending_team,game_state,winning_team,wallTime\n")
                    for event in events:
                        gameDecided = event['gameDecided']
                        attacking_team = gameDecided['spikeMode']['attackingTeam']['value']
                        defending_team = gameDecided['spikeMode']['defendingTeam']['value']
                        winning_round_number = gameDecided['spikeMode']['currentRound']
                        game_state = gameDecided['state']
                        winning_team = gameDecided['winningTeam']['value']
                        wallTime = event['metadata']['wallTime']
                        f.write(f"{platformGameId},{winning_round_number},{attacking_team},{defending_team},{game_state},{winning_team},{wallTime}\n")