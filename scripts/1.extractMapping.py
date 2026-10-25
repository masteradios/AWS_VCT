import pandas as pd
import os
leagues =['game-changers','vct-international', 'vct-challengers']
for league in leagues:

    directory = f"/home/adityak/projects/aws-hackathon/{league}/esports-data"


    tournamentsDf=pd.read_json(f"{directory}/tournaments.json",dtype=False)
    leaguesDf=pd.read_json(f"{directory}/leagues.json",dtype=False)[['league_id','name']]
    def extractTeamKeyandValue(row):
            results=[]
            for key,value in row.teamMapping.items():
                results.append({"platformGameId":row.platformGameId,"teamKey":key,"teamId":value})
            return results
            

    def extractPlayerKeyandValue(row):
            results=[]
            for key,value in row.participantMapping.items():
                results.append({"platformGameId":row.platformGameId,"playerKey":key,"playerId":value})
            return results



    df=pd.read_json(f"{directory}/mapping_data.json",dtype=False)[['platformGameId','teamMapping','participantMapping','tournamentId']]

    teams=[item for sublist in df.apply(extractTeamKeyandValue, axis=1) for item in sublist]
    teamsDf=pd.DataFrame(teams)

    players=[item for sublist in df.apply(extractPlayerKeyandValue, axis=1) for item in sublist]
    playersDf=pd.DataFrame(players)


    gamesDf=df[['platformGameId','tournamentId']]

    resultsDir=f"/home/adityak/projects/aws-hackathon/results/league={league}"
    os.makedirs(resultsDir,exist_ok=True)

    gamesdf = (
          pd.merge(
          gamesDf, 
          tournamentsDf,
          left_on='tournamentId',
          right_on='id',how='left',
          ).rename(columns={"name": "tournament_name"}).
          merge(
                leaguesDf,
                how="left",
                on="league_id").rename(columns={"name": "league_name"})
                [['platformGameId','tournamentId','league_id','tournament_name','league_name']]

        )
    teams_json= pd.read_json(f'{directory}/teams.json',dtype=False).drop_duplicates(['id'])
    #teams_csv = pd.read_csv
    teamsDf.to_csv(f'{resultsDir}/teams.csv',index=False)
    teams_enriched=pd.merge(
          teamsDf, 
          teams_json,
          left_on='teamId',
          right_on='id',how='left',
          ).drop(columns=['acronym','home_league_id','dark_logo_url','light_logo_url','slug','id']).rename(columns={"name":"team_name"})
    #teams_enriched.to_csv(f'{resultsDir}/teams.csv',index=False)
    playersDf.to_csv(f'{resultsDir}/players.csv',index=False)
    gamesdf.to_csv(f'{resultsDir}/games.csv',index=False)
