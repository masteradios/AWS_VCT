import json
import os
from pathlib import Path
from collections import defaultdict
import pandas as pd

leagues = ['game-changers']
YEAR = 2023
COMMON_KEYS = {"platformGameId", "metadata"}
required = [
    'configuration', 'damageEvent', 'gameDecided', 'gamePhase',
    'playerDied', 'roundCeremony', 'roundDecided', 'roundStarted',
    'snapshot', 'spikeStatus',
]

for league in leagues:
    gamesDir = f"/home/adityak/projects/aws-hackathon/{league}/games/{YEAR}"
    gamesDirectory = Path(gamesDir)

    for file_path in gamesDirectory.glob('val:*.json'):
        if not file_path.is_file():
            continue

        print(f"File Name: {file_path.name.split(".")[0]}")

        resultsDir = f"/home/adityak/projects/aws-hackathon/results/json/league={league}/year={YEAR}"
        os.makedirs(resultsDir, exist_ok=True)

        with open(file_path) as f:
            events = json.load(f)

        events_by_type = defaultdict(list)
        for e in events:
            event_keys = set(e.keys()) - COMMON_KEYS
            if len(event_keys) == 1:
                events_by_type[event_keys.pop()].append(e)
            else:
                events_by_type["_unrecognized"].append(e)

        for etype, evs in events_by_type.items():
            if etype == "_unrecognized" or etype not in required:
                continue
            eventDir=f"{resultsDir}/{etype}"
            os.makedirs(eventDir, exist_ok=True)
            with open(f"{eventDir}/{file_path.name.replace('.json','')}:{etype}.json", "w", encoding="utf-8") as final:
                json.dump(evs, final, indent=2)