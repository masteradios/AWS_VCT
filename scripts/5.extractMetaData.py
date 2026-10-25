import pandas as pd

leagues =['game-changers','vct-international', 'vct-challengers']
results=[]
for league in leagues:
    directory = f"/home/adityak/projects/aws-hackathon/{league}/esports-data"
    leaguesDf =pd.read_json(f"{directory}/leagues.json")[['league_id','region','name']]
    number_of_leagues=leaguesDf['league_id'].count()

    tournament=pd.read_json(f"{directory}/tournaments.json")
    number_of_tournaments=tournament['id'].count()


    players=pd.read_json(f"{directory}/players.json")
    number_of_players=players['id'].count()


    teams=pd.read_json(f"{directory}/teams.json")
    number_of_teams=teams['id'].count()
    results.append({
        "league": league,
        "total_leagues": number_of_leagues,
        "total_players": number_of_players,
        "total_tournaments": number_of_tournaments,
        "total_teams": number_of_teams,
    })

game_info = pd.DataFrame(results)
resultsDir=f"/home/adityak/projects/aws-hackathon/results/{league}"
game_info.to_csv(f"{resultsDir}/games_metadata.csv",index=False)