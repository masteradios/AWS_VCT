from pathlib import Path
import psycopg2 as pg

db_params = {
    "dbname": "aws_vct",
    "user": "postgres",
    "password": "postgres",
    "host": "localhost",
    "port": "5432"
}

leagues = ['game-changers']
YEAR = 2023


def insert_into_table(conn, cursor, table_name, csv_file_path): 
    try:
        path_obj = Path(csv_file_path)
        if not path_obj.exists():
            print(f"File not found: {csv_file_path}, skipping.")
            return

        with open(csv_file_path, 'r', encoding='utf-8') as f:
            next(f)  # Skip the header row
            cursor.copy_from(f, table_name, sep=',', null='')
            
        conn.commit()
        print(f"Bulk insert completed for {path_obj.name} into {table_name}.")
        
    except Exception as e:
        print(f"An error occurred with {csv_file_path}: {e}")
        conn.rollback()


if __name__ == "__main__":
    conn = None
    cursor = None
    try:
        conn = pg.connect(**db_params)
        cursor = conn.cursor()
        
        for league in leagues:
            text_base_dir = f"/home/adityak/projects/aws-hackathon/results/text/league={league}/year={YEAR}"
            #required_base_dir = f"/home/adityak/projects/aws-hackathon/results/league={league}"
            
            # Discover game IDs using one of the updated event folders as a reference
            damage_dir = Path(f"{text_base_dir}/damageEvent_updated")
            if damage_dir.exists():
                for file_path in damage_dir.glob('*:damageEvent_updated.txt'):
                    if not file_path.is_file():
                        continue
                    
                    # Extract game ID from filename (e.g., ecaa1621-bb56-4daa-9cdf-23571c0cd86e)
                    game_id = file_path.name.rsplit(':', 1)[0]
                    
                    # New text folder paths
                    rounds_event_path = f"{text_base_dir}/roundsEventUpdated/{game_id}:roundsEventUpdated.txt"
                    spike_status_path = f"{text_base_dir}/spikeStatus_updated/{game_id}:spikeStatus_updated.txt"
                    damage_event_path = f"{text_base_dir}/damageEvent_updated/{game_id}:damageEvent_updated.txt"
                    player_died_path = f"{text_base_dir}/playerDied_updated/{game_id}:playerDied_updated.txt"
                    
                    # Auxiliary game files path
                    game_decided_path = f"{text_base_dir}/gameDecided/{game_id}:gameDecided.txt"
                    players_csv_path = f"{text_base_dir}/players.csv"
                    
                    # Execute bulk insertions
                    insert_into_table(conn, cursor, "game_rounds_event", rounds_event_path)
                    insert_into_table(conn, cursor, "game_spike_status_event", spike_status_path)
                    insert_into_table(conn, cursor, "game_damage_event", damage_event_path)
                    insert_into_table(conn, cursor, "game_player_died_event", player_died_path)
                    insert_into_table(conn, cursor, "game_game_decided_event", game_decided_path)
                    insert_into_table(conn, cursor, "game_players_event", players_csv_path)

            # Insert league-level team enriched data
            teams_csv_path = f"/home/adityak/projects/aws-hackathon/results/league={league}/teams_enriched.csv"
            insert_into_table(conn, cursor, "game_team_event", teams_csv_path)

    except Exception as e:
        print(f"An error occurred: {e}")
        if conn:
            conn.rollback()

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()