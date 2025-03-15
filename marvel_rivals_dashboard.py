#!/usr/bin/env python3
"""
Marvel Rivals Tournament Dashboard

This module loads player JSON data, fetches detailed match data via Selenium,
clusters tournament matches, extracts per‑10 and aggregate player statistics,
and displays an interactive dashboard where you can sort team members by any
displayed stat and view an overview of team performance.
"""

import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

import streamlit as st
import pandas as pd
from selenium import webdriver
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.firefox.service import Service
from webdriver_manager.firefox import GeckoDriverManager


# 2. Replace the create_chrome_options function with this
def create_firefox_options():
    firefox_options = FirefoxOptions()
    firefox_options.add_argument("--headless")
    firefox_options.add_argument("--no-sandbox")
    firefox_options.add_argument("--disable-dev-shm-usage")
    return firefox_options

# 3. Replace the driver initialization (around line 30) with this
try:
    service = Service(GeckoDriverManager().install())
    driver = webdriver.Firefox(service=service, options=create_firefox_options())
except Exception as e:
    print(f"Error initializing WebDriver: {e}")


def load_data(file) -> dict:
    """Load JSON data from a file-like object."""
    return json.load(file)


class SeleniumManager:
    """
    Context manager for Selenium WebDriver using Firefox.

    This class simplifies driver creation and cleanup.
    """

    def __init__(self):
        self.driver = webdriver.Firefox(options=create_firefox_options())
        # Hide Selenium property for more natural behavior.
        self.driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    def __enter__(self):
        return self.driver

    def __exit__(self, exc_type, exc_value, traceback):
        self.driver.quit()


def fetch_detailed_match_data(match_id, driver=None):
    """
    Fetch detailed match data using Selenium.
    Returns a dictionary with the match details.
    """
    should_close_driver = False
    if driver is None:
        driver = webdriver.Firefox(options=create_firefox_options())
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        should_close_driver = True

    try:
        url = f"https://api.tracker.gg/api/v2/marvel-rivals/standard/matches/{match_id}"
        st.info(f"Fetching detailed data for match {match_id}")
        driver.get(url)
        time.sleep(3)  # wait for content to load

        try:
            json_text = driver.find_element(By.TAG_NAME, "pre").text
        except NoSuchElementException:
            page_source = driver.page_source
            start = page_source.find('{')
            end = page_source.rfind('}') + 1
            if start >= 0 and end > start:
                json_text = page_source[start:end]
            else:
                raise Exception("Could not find JSON content in the page")

        data = json.loads(json_text)
        return data.get('data', data)
    except Exception as e:
        st.error(f"Error fetching match {match_id}: {e}")
        return None
    finally:
        if should_close_driver:
            driver.quit()


def cluster_tournament_matches(data):
    """
    Group tournament matches into clusters based on time proximity.
    Matches that occur within 24 hours are clustered as part of the same tournament.
    Returns a list of tournament clusters (each a list of match dictionaries).
    """
    matches = []
    for match in data.get('matches', []):
        if match['attributes'].get('mode') == 'tournament':
            timestamp = datetime.fromisoformat(
                match['metadata']['timestamp'].replace('Z', '+00:00')
            )
            matches.append({
                'match_id': match['attributes']['id'],
                'timestamp': timestamp,
                'map_name': match['metadata']['mapName'],
                'map_mode': match['metadata']['mapModeName'],
                'duration': int(match['metadata']['duration']),
                'winning_team': match['metadata']['winningTeamId'],
                'scores': match['metadata']['scores'],
                'segments': match.get('segments', [])
            })

    matches.sort(key=lambda m: m['timestamp'])
    tournaments, current = [], []
    for match in matches:
        if not current:
            current.append(match)
        else:
            if match['timestamp'] - current[-1]['timestamp'] <= timedelta(days=1):
                current.append(match)
            else:
                tournaments.append(current)
                current = [match]
    if current:
        tournaments.append(current)
    return tournaments


def calculate_per_10(value, duration_seconds):
    """
    Calculate a per-10-minute rate based on match duration.
    """
    minutes = duration_seconds / 60
    return (value / minutes) * 10 if minutes > 0 else 0


def extract_player_stats(match_data, player_ign):
    """
    Extract detailed player statistics from a match.
    Returns two lists: team_stats and opponent_stats.
    """
    team_stats = []
    opponent_stats = []
    match_duration = int(match_data.get('metadata', {}).get('duration', 0))

    # Determine the main player's team ID.
    main_team = None
    for segment in match_data.get('segments', []):
        if segment.get('type') == 'player' and (
            segment['metadata']['platformInfo']['platformUserHandle'] == player_ign
        ):
            main_team = segment['metadata'].get('teamId')
            break

    # Process all player segments.
    for segment in match_data.get('segments', []):
        if segment.get('type') != 'player':
            continue

        team_id = segment['metadata'].get('teamId')
        player_info = {
            'name': segment['metadata']['platformInfo']['platformUserHandle'],
            'team_id': team_id,
            'result': segment['metadata']['result'],
            'heroes': [h['name'] for h in segment['metadata'].get('heroes', [])] or ["Unknown"],
            'is_mvp': segment['metadata'].get('isMvp', 0),
            'is_svp': segment['metadata'].get('isSvp', 0),
        }
        stats = segment.get('stats', {})
        for key in [
            'kills', 'deaths', 'assists', 'kdRatio', 'kdaRatio',
            'totalHeroDamage', 'totalHeroHeal', 'totalDamageTaken',
            'lastKills', 'soloKills', 'headKills', 'sessionSurvivalKills',
            'maxContinueKills', 'mainAttacks', 'mainAttackHits',
            'continueKills3', 'shieldHits', 'chaosHits', 'summonerHits'
        ]:
            if key in stats:
                player_info[key] = stats[key].get('value', 0)

        # Calculate per-10-minute statistics.
        if match_duration:
            player_info['kills_per_10'] = calculate_per_10(
                player_info.get('kills', 0), match_duration
            )
            player_info['deaths_per_10'] = calculate_per_10(
                player_info.get('deaths', 0), match_duration
            )
            player_info['assists_per_10'] = calculate_per_10(
                player_info.get('assists', 0), match_duration
            )
            player_info['damage_per_10'] = calculate_per_10(
                player_info.get('totalHeroDamage', 0), match_duration
            )
            player_info['healing_per_10'] = calculate_per_10(
                player_info.get('totalHeroHeal', 0), match_duration
            )
            player_info['damage_taken_per_10'] = calculate_per_10(
                player_info.get('totalDamageTaken', 0), match_duration
            )
            player_info['soloKills_per_10'] = calculate_per_10(
                player_info.get('soloKills', 0), match_duration
            )

        # Calculate accuracy if possible.
        if player_info.get('mainAttacks', 0) > 0:
            player_info['accuracy'] = (
                player_info.get('mainAttackHits', 0) /
                player_info.get('mainAttacks', 0)
            ) * 100
        else:
            player_info['accuracy'] = 0

        # Default kill participation; computed later.
        player_info['kill_participation'] = 0

        # Classify into team or opponent stats.
        if main_team is not None and team_id == main_team:
            team_stats.append(player_info)
        else:
            opponent_stats.append(player_info)

    # Calculate kill participation for each side.
    for group in (team_stats, opponent_stats):
        total_kills = sum(p.get('kills', 0) for p in group)
        if total_kills:
            for p in group:
                p['kill_participation'] = (
                    (p.get('kills', 0) + p.get('assists', 0)) / total_kills
                ) * 100

    return team_stats, opponent_stats


def format_duration(seconds):
    """Convert seconds into MM:SS string format."""
    minutes, sec = divmod(seconds, 60)
    return f"{minutes:02d}:{sec:02d}"


def analyze_tournaments(data, detailed=True, cache_dir="match_cache"):
    """
    Analyze tournament data for a player and aggregate stats.
    Returns a list of tournament analysis results.
    """
    player_ign = data.get('ign')
    player_team = None

    if cache_dir and not os.path.exists(cache_dir):
        os.makedirs(cache_dir)

    # Determine player's team ID from the first overview segment.
    for match in data.get('matches', []):
        for segment in match.get('segments', []):
            if segment.get('type') == 'overview' and (
                segment['metadata']['platformInfo']['platformUserHandle'] == player_ign
            ):
                player_team = segment['metadata'].get('teamId')
                break
        if player_team is not None:
            break

    if player_team is None:
        raise ValueError("Player team could not be identified.")

    tournaments_cluster = cluster_tournament_matches(data)
    tournament_results = []
    driver = None

    if detailed:
        driver = webdriver.Firefox(options=create_firefox_options())
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

    try:
        for idx, tournament in enumerate(tournaments_cluster):
            tournament_info = {
                'id': idx + 1,
                'start_date': tournament[0]['timestamp'],
                'end_date': tournament[-1]['timestamp'],
                'match_count': len(tournament),
                'matches': [],
                'player_stats': defaultdict(lambda: {
                    'matches_played': 0,
                    'wins': 0,
                    'losses': 0,
                    'heroes_played': set(),
                    'total_kills': 0,
                    'total_last_kills': 0,
                    'total_deaths': 0,
                    'total_assists': 0,
                    'total_damage': 0,
                    'total_healing': 0,
                    'total_damage_taken': 0,
                    'total_solo_kills': 0,
                    'total_head_kills': 0,
                    'total_shield_hits': 0,
                    'total_kill_participation': 0,
                    'total_minutes': 0,
                    'avg_kda': 0,
                    'best_kda': 0,
                    'best_hero': None,
                    'match_performances': []
                })
            }
            for match in tournament:
                match_id = match['match_id']
                cache_file = os.path.join(cache_dir, f"match_{match_id}.json") if cache_dir else None
                detailed_match = None

                if cache_file and os.path.exists(cache_file):
                    with open(cache_file, 'r') as f:
                        detailed_match = json.load(f)

                if detailed and not detailed_match:
                    detailed_match = fetch_detailed_match_data(match_id, driver)
                    if cache_file and detailed_match:
                        with open(cache_file, 'w') as f:
                            json.dump(detailed_match, f, indent=2)

                # Extract stats using detailed data if available.
                team_stats, opponent_stats = [], []
                if detailed_match:
                    team_stats, opponent_stats = extract_player_stats(detailed_match, player_ign)
                else:
                    # Fallback using overview segments.
                    for segment in match.get('segments', []):
                        if segment.get('type') == 'overview':
                            info = {
                                'name': segment['metadata']['platformInfo']['platformUserHandle'],
                                'team_id': segment['metadata'].get('teamId'),
                                'result': segment['metadata']['result'],
                                'heroes': [h['name'] for h in segment['metadata'].get('heroes', [])] or ["Unknown"],
                                'kills': segment['stats']['kills']['value'],
                                'deaths': segment['stats']['deaths']['value'],
                                'assists': segment['stats']['assists']['value'],
                                'kdaRatio': segment['stats']['kdaRatio']['value'],
                                'totalHeroDamage': segment['stats']['totalHeroDamage']['value'],
                                'totalHeroHeal': segment['stats'].get('totalHeroHeal', {}).get('value', 0)
                            }
                            if info['team_id'] == player_team:
                                team_stats.append(info)
                            else:
                                opponent_stats.append(info)

                duration_sec = match['duration']
                minutes = duration_sec / 60

                # Update aggregated player stats.
                for player in team_stats:
                    stats = tournament_info['player_stats'][player['name']]
                    stats['matches_played'] += 1
                    stats['total_minutes'] += minutes
                    if player['result'] == 'win':
                        stats['wins'] += 1
                    else:
                        stats['losses'] += 1
                    for hero in player.get('heroes', []):
                        stats['heroes_played'].add(hero)
                    stats['total_kills'] += player.get('kills', 0)
                    stats['total_deaths'] += player.get('deaths', 0)
                    stats['total_assists'] += player.get('assists', 0)
                    stats['total_damage'] += player.get('totalHeroDamage', 0)
                    stats['total_healing'] += player.get('totalHeroHeal', 0)
                    stats['total_damage_taken'] += player.get('totalDamageTaken', 0)
                    stats['total_solo_kills'] += player.get('soloKills', 0)
                    stats['total_last_kills'] += player.get('lastKills', 0)
                    stats['total_head_kills'] += player.get('headKills', 0)
                    stats['total_shield_hits'] += player.get('shieldHits', 0)
                    stats['total_kill_participation'] += player.get('kill_participation', 0)
                    kda = player.get('kdaRatio', 0)
                    stats['avg_kda'] += kda
                    if kda > stats['best_kda']:
                        stats['best_kda'] = kda
                        stats['best_hero'] = player.get('heroes', ["Unknown"])[0]
                    # Save this match performance.
                    stats['match_performances'].append({
                        'match_id': match_id,
                        'hero': player.get('heroes', ["Unknown"])[0],
                        'result': player['result'],
                        'kills': player.get('kills', 0),
                        'deaths': player.get('deaths', 0),
                        'assists': player.get('assists', 0),
                        'kda': kda,
                        'damage_per_10': calculate_per_10(player.get('totalHeroDamage', 0), duration_sec),
                        'healing_per_10': calculate_per_10(player.get('totalHeroHeal', 0), duration_sec),
                        'kills_per_10': calculate_per_10(player.get('kills', 0), duration_sec),
                        'deaths_per_10': calculate_per_10(player.get('deaths', 0), duration_sec),
                        'assists_per_10': calculate_per_10(player.get('assists', 0), duration_sec),
                        'kill_participation': player.get('kill_participation', 0),
                        'accuracy': player.get('accuracy', 0),
                        'solo_kills': player.get('soloKills', 0),
                        'soloKills_per_10': calculate_per_10(player.get('soloKills', 0), duration_sec),
                        'last_kills': player.get('lastKills', 0),
                        'fb_per_10': calculate_per_10(player.get('lastKills', 0), duration_sec),
                        'head_kills': player.get('headKills', 0),
                        'is_mvp': player.get('is_mvp', 0),
                        'is_svp': player.get('is_svp', 0)
                    })

                match_result = "Unknown"
                for player in team_stats + opponent_stats:
                    if player['name'] == player_ign:
                        match_result = player['result']
                        break

                match_info = {
                    'id': match_id,
                    'map': match['map_name'],
                    'mode': match['map_mode'],
                    'result': match_result,
                    'duration': format_duration(duration_sec),
                    'duration_seconds': duration_sec,
                    'score': (
                        f"{match['scores'][int(player_team)]}-"
                        f"{match['scores'][1 - int(player_team)]}"
                        if match.get('scores') else "N/A"
                    ),
                    'timestamp': match['timestamp'].strftime('%Y-%m-%d %H:%M:%S'),
                    'team_stats': team_stats,
                    'opponent_stats': opponent_stats
                }
                tournament_info['matches'].append(match_info)

            # Finalize aggregated stats.
            for stats in tournament_info['player_stats'].values():
                if stats['matches_played'] > 0:
                    stats['avg_kda'] /= stats['matches_played']
                    stats['avg_kills'] = stats['total_kills'] / stats['matches_played']
                    stats['avg_deaths'] = stats['total_deaths'] / stats['matches_played']
                    stats['avg_assists'] = stats['total_assists'] / stats['matches_played']
                    stats['avg_damage'] = stats['total_damage'] / stats['matches_played']
                    stats['avg_healing'] = stats['total_healing'] / stats['matches_played']
                    stats['avg_damage_taken'] = stats['total_damage_taken'] / stats['matches_played']
                    stats['avg_solo_kills'] = stats['total_solo_kills'] / stats['matches_played']
                    stats['avg_last_kills'] = stats['total_last_kills'] / stats['matches_played']
                    stats['avg_head_kills'] = stats['total_head_kills'] / stats['matches_played']
                    stats['avg_shield_hits'] = stats['total_shield_hits'] / stats['matches_played']
                    stats['avg_kill_participation'] = stats['total_kill_participation'] / stats['matches_played']
                    if stats['total_minutes'] > 0:
                        total_min = stats['total_minutes']
                        stats['avg_kills_per_10'] = (stats['total_kills'] / total_min) * 10
                        stats['avg_fb_per_10'] = (stats['total_last_kills'] / total_min) * 10
                        stats['avg_solo_kills_per_10'] = (stats['total_solo_kills'] / total_min) * 10
                        stats['avg_deaths_per_10'] = (stats['total_deaths'] / total_min) * 10
                        stats['avg_assists_per_10'] = (stats['total_assists'] / total_min) * 10
                        stats['avg_damage_per_10'] = round((stats['total_damage'] / total_min) * 10, 0)
                        stats['avg_healing_per_10'] = round((stats['total_healing'] / total_min) * 10, 0)
                        stats['avg_damage_taken_per_10'] = round((stats['total_damage_taken'] / total_min) * 10, 0)
                    stats['heroes_played'] = list(stats['heroes_played'])
                    stats['win_rate'] = (stats['wins'] / stats['matches_played']) * 100

            tournament_results.append(tournament_info)
        return tournament_results
    finally:
        if driver:
            driver.quit()


def player_stats_to_df(player_stats: dict) -> pd.DataFrame:
    """Convert aggregated player stats into a Pandas DataFrame."""
    rows = []
    for player, stats in player_stats.items():
        rows.append({
            "Player": player,
            "Matches Played": stats["matches_played"],
            "Wins": stats["wins"],
            "Losses": stats["losses"],
            "Win Rate (%)": round(stats["win_rate"], 1),
            "Best Hero": stats["best_hero"] if stats["best_hero"] else "N/A",
            "Best KDA": round(stats["best_kda"], 2),
            "Avg KDA": round(stats["avg_kda"], 2),
            "Avg Final Blows": round(stats["avg_last_kills"], 2),
            "Avg Solo Kils": round(stats["avg_solo_kills"], 2),
            "K/D/A": f"{stats['avg_kills']:.1f}/{stats['avg_deaths']:.1f}/{stats['avg_assists']:.1f}",
            "Avg Damage": round(stats["avg_damage"], 0),
            "Avg Healing": round(stats["avg_healing"], 0),
            "Avg Kill Participation (%)": round(stats["avg_kill_participation"], 1)
        })
    return pd.DataFrame(rows)


def match_performances_to_df(match_performances: list) -> pd.DataFrame:
    """Convert a list of match performance dictionaries into a DataFrame."""
    return pd.DataFrame(match_performances)


# ------------- Streamlit Dashboard ------------- #

st.set_page_config(page_title="Marvel Rivals Tournament Dashboard", layout="wide")
st.title("Marvel Rivals Tournament Dashboard")

st.markdown("""
This dashboard displays aggregated tournament stats and team performance.
Upload your player JSON data below to begin.
""")

uploaded_file = st.file_uploader("Choose a JSON file", type=["json"])

if uploaded_file is not None:
    try:
        data = load_data(uploaded_file)
        player_ign = data.get('ign', 'unknown_player')
        st.success(f"Data loaded for player: **{player_ign}**")
    except Exception as e:
        st.error(f"Error loading file: {e}")
        st.stop()

    # Option to run detailed analysis (will fetch match data via Selenium)
    detailed_mode = st.checkbox("Use detailed match data (may take longer)", value=True)

    with st.spinner("Analyzing tournaments..."):
        tournaments = analyze_tournaments(data, detailed=detailed_mode)

    if not tournaments:
        st.warning("No tournament matches found.")
        st.stop()

    # If more than one tournament, let the user select which one to view.
    tournament_options = {
        f"Tournament #{t['id']} ({t['start_date'].strftime('%Y-%m-%d')} - {t['end_date'].strftime('%Y-%m-%d')})": t
        for t in tournaments
    }
    selected_tournament_label = st.selectbox("Select Tournament", list(tournament_options.keys()))
    tournament = tournament_options[selected_tournament_label]

    st.subheader(f"Tournament #{tournament['id']} Overview")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"**Date:** {tournament['start_date'].strftime('%Y-%m-%d')} to {tournament['end_date'].strftime('%Y-%m-%d')}")
    with col2:
        st.markdown(f"**Matches:** {tournament['match_count']}")
    with col3:
        team_wins = sum(1 for m in tournament['matches'] if m['result'] == 'win')
        team_losses = tournament['match_count'] - team_wins
        st.markdown(f"**Team Record:** {team_wins} - {team_losses}")

    st.markdown("---")
    st.subheader("Team Performance")
    player_stats_df = player_stats_to_df(tournament['player_stats'])
    st.dataframe(player_stats_df, use_container_width=True)

    st.markdown("Use the table header to sort by any column.")

    # Optional: select a team member to view detailed match performances.
    team_members = sorted(list(tournament['player_stats'].keys()))
    selected_member = st.selectbox("View detailed match performances for:", team_members)
    member_stats = tournament['player_stats'][selected_member]
    if member_stats.get("match_performances"):
        st.subheader(f"Match Performances for {selected_member}")
        perf_df = match_performances_to_df(member_stats["match_performances"])
        st.dataframe(perf_df, use_container_width=True)
    else:
        st.info("No match performance data available for this player.")
else:
    st.info("Awaiting JSON file upload.")
