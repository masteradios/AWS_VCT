import pandas as pd
import os
leagues =['game-changers',
          'vct-international', 'vct-challengers'
          ]
for league in leagues:

    mainDirectory = f"/home/adityak/projects/aws-hackathon/results/league={league}/"
    lookupDirectory = f"/home/adityak/projects/aws-hackathon/{league}/esports-data"
    
    # 1. Team Enrichment
    teams_json= pd.read_json(f'{lookupDirectory}/teams.json',dtype={"id":"str"}).drop_duplicates(['id'])
    teams_csv = pd.read_csv(f'{mainDirectory}/teams.csv',dtype={"teamId":"str"})
    teams_enriched=pd.merge(
          teams_csv, 
          teams_json,
          left_on='teamId',
          right_on='id',how='left',
          ).drop(columns=['acronym','home_league_id','dark_logo_url','light_logo_url','slug','id']).rename(columns={"name":"team_name"})
    teams_enriched['league']=league
    teams_enriched.to_csv(f'{mainDirectory}/teams_enriched.csv',index=False)
    
    
    # 2. Player Enrichment
    players_json= pd.read_json(f'{lookupDirectory}/players.json').drop_duplicates(['id']).astype(str)
    players_csv = pd.read_csv(f'{mainDirectory}/players.csv').astype(str)
    players_enriched=pd.merge(
          players_csv, 
          players_json,
          left_on='playerId',
          right_on='id',how='left',
          ).drop(columns=['status','photo_url','created_at','updated_at','id'])
    players_enriched['league']=league
    players_enriched.to_csv(f'{mainDirectory}/players_enriched.csv',index=False)
    os.remove(f'{mainDirectory}/players.csv')
    os.remove(f'{mainDirectory}/teams.csv')
