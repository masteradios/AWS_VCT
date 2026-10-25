-- THIS IS SAMPLE POSTGRES DB CONFIGURATION FOR LOCAL DEVELOPMENT
create database aws_vct;
\c aws_vct;
create table games (
platformGameId varchar primary key,
tournament_id varchar,
league_id varchar,
tournament_name varchar,
league_name varchar);


create table players (
id varchar ,
handle varchar,
first_name varchar,
last_name varchar,
home_team_id varchar,
primary key (id)
);


create table teams(
  id  varchar,
  acronym varchar,
  home_league_id varchar,
  name varchar,
  primary key (id)

);

create table game_team_event (
platformGameId varchar ,
team_key varchar,
team_id varchar,
team_name varchar,
league_name  varchar,
primary key (platformGameId,team_id)
);

create table game_players_event(
platformGameId varchar,
wallTime timestamp,
team_key varchar,
player_key varchar,
player_id varchar,
handle varchar,
first_name varchar,
last_name varchar,
home_team_id varchar,
current_game_team_id varchar,
team_name varchar,
primary key (platformGameId,current_game_team_id,player_id)
);


create table game_rounds_event(

platformGameId varchar,
roundNumber varchar,
attackingTeam varchar,
defendingTeam varchar,
round_status varchar,
winning_team varchar,
cause varchar,
wallTime timestamp,
primary key (platformGameId,roundNumber,wallTime)

);

create table game_damage_event(
platformGameId varchar,
roundNumber varchar,
damage_location varchar,
causer_id varchar,
victim_id varchar,
damage_amount varchar,
kill_event varchar,
wallTime TIMESTAMP,
primary key (platformGameId,wallTime)
);


create table game_game_decided_event(
platformGameId varchar,
winning_round_number varchar,
attacking_team varchar,
defending_team varchar,
game_state varchar,
winning_team varchar,
wallTime timestamp,
primary key (platformGameId)
);

create table game_player_died_event (

platformGameId varchar,
roundNumber varchar,
deceased_id varchar,
killer_id varchar,
assistants int[],
wallTime timestamp,
primary key(platformGameId,wallTime)


);

create table game_spike_status_event(
platformGameId varchar,
roundNumber varchar,
spike_status varchar,
carrier varchar,
wallTime TIMESTAMP,
primary key (platformGameId,wallTime)
);



