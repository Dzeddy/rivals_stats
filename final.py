#!/usr/bin/env python3
"""
Refactored Marvel Rivals Tournament Analysis

This module loads player JSON data, fetches detailed match data via Selenium,
clusters tournament matches, extracts per‑10 and aggregate player statistics,
and generates both match‐ and tournament‑level reports.
"""

import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta

from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By


def load_data(file_path):
    """Load JSON data from a file."""
    with open(file_path, 'r') as f:
        return json.load(f)


def create_chrome_options():
    """Configure and return Chrome options for Selenium."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    )
    return options


class SeleniumManager:
    """
    Context manager for Selenium WebDriver.

    This class simplifies driver creation and cleanup.
    """

    def __init__(self):
        self.driver = webdriver.Chrome(options=create_chrome_options())
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

    Args:
        match_id: ID of the match to fetch.
        driver: Selenium WebDriver instance; if None, a temporary one is created.

    Returns:
        A dictionary with detailed match data.
    """
    should_close_driver = False
    if driver is None:
        driver = webdriver.Chrome(options=create_chrome_options())
        driver.execute_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        should_close_driver = True

    try:
        url = f"https://api.tracker.gg/api/v2/marvel-rivals/standard/matches/{match_id}"
        print(f"Fetching detailed data for match {match_id}")
        driver.get(url)
        time.sleep(3)

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
        print(f"Error fetching match {match_id}: {e}")
        return None
    finally:
        if should_close_driver:
            driver.quit()


def cluster_tournament_matches(data):
    """
    Group tournament matches into clusters based on time proximity.

    Matches that occur within 24 hours are clustered as part of the same
    tournament.

    Returns:
        A list of tournament clusters (each a list of match dictionaries).
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

    Args:
        value: The stat value.
        duration_seconds: Duration of the match in seconds.

    Returns:
        Stat rate per 10 minutes.
    """
    minutes = duration_seconds / 60
    return (value / minutes) * 10 if minutes > 0 else 0


def extract_player_stats(match_data, player_ign):
    """
    Extract detailed player statistics from a match.

    Uses the main player's IGN to separate team and opponent stats and adds
    per-10-minute calculations.
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


def generate_match_report(match_info, filter_teammates=False, player_ign=None):
    """
    Generate a detailed match report.

    Args:
        match_info: Dictionary containing match details.
        filter_teammates: If True, include only teammates of `player_ign`.
        player_ign: The main player's in-game name.

    Returns:
        A formatted string report.
    """
    lines = [
        f"Match: {match_info['map']} ({match_info['mode']})",
        f"Date: {match_info['timestamp']}",
        f"Duration: {match_info['duration']}",
        f"Result: {match_info['result'].upper()} ({match_info['score']})",
        ""
    ]

    # Filter team stats if required.
    if filter_teammates and player_ign:
        team_id = next(
            (p['team_id'] for p in match_info['team_stats']
             if p['name'] == player_ign),
            None
        )
        teammates = [p for p in match_info['team_stats']
                     if p['team_id'] == team_id] if team_id is not None \
            else match_info['team_stats']
    else:
        teammates = match_info['team_stats']

    header = (
        "{:<15} {:<12} {:<8} {:<8} {:<8} {:<7} {:<8} {:<8} {:<10} {:<8}"
    ).format("Player", "Hero", "K(P10)", "D(P10)", "A(P10)", "KDA",
             "DMG/10", "HEAL/10", "K.Part%", "Acc%")
    lines.append("Team Performance:")
    lines.append(header)
    lines.append("-" * len(header))
    for player in teammates:
        hero = player['heroes'][0] if player['heroes'] else "Unknown"
        kills_str = f"{player.get('kills', 0)}({player.get('kills_per_10', 0):.1f})"
        deaths_str = f"{player.get('deaths', 0)}({player.get('deaths_per_10', 0):.1f})"
        assists_str = f"{player.get('assists', 0)}({player.get('assists_per_10', 0):.1f})"
        special = "MVP" if player.get('is_mvp') else "SVP" if player.get('is_svp') else ""
        lines.append(
            "{:<15} {:<12} {:<8} {:<8} {:<8} {:<7.2f} {:<8.0f} {:<8.0f} "
            "{:<10.1f} {:<8.1f} {}".format(
                player['name'], hero, kills_str, deaths_str, assists_str,
                player.get('kdaRatio', 0), player.get('damage_per_10', 0),
                player.get('healing_per_10', 0),
                player.get('kill_participation', 0),
                player.get('accuracy', 0), special
            )
        )
    lines.append("")

    # Additional team stats.
    header2 = (
        "{:<15} {:<8} {:<8} {:<8} {:<10} {:<8} {:<8}"
    ).format("Player", "SoloK", "LastK", "HeadK", "DMG Taken", "ShieldH", "Triple+")
    lines.append("Additional Team Stats:")
    lines.append(header2)
    lines.append("-" * len(header2))
    for player in teammates:
        multi_kills = player.get('continueKills3', 0)
        lines.append(
            "{:<15} {:<8} {:<8} {:<8} {:<10.0f} {:<8} {:<8}".format(
                player['name'],
                player.get('soloKills', 0),
                player.get('lastKills', 0),
                player.get('headKills', 0),
                player.get('totalDamageTaken', 0),
                player.get('shieldHits', 0),
                multi_kills
            )
        )
    lines.append("")

    # Opponent performance (if available).
    if match_info.get('opponent_stats'):
        header3 = (
            "{:<15} {:<12} {:<8} {:<8} {:<8} {:<7} {:<8} {:<8} {:<10} {:<8}"
        ).format("Player", "Hero", "K(P10)", "D(P10)", "A(P10)", "KDA",
                 "DMG/10", "HEAL/10", "K.Part%", "Acc%")
        lines.append("Opponent Performance:")
        lines.append(header3)
        lines.append("-" * len(header3))
        for player in match_info['opponent_stats']:
            hero = player['heroes'][0] if player['heroes'] else "Unknown"
            kills_str = f"{player.get('kills', 0)}({player.get('kills_per_10', 0):.1f})"
            deaths_str = f"{player.get('deaths', 0)}({player.get('deaths_per_10', 0):.1f})"
            assists_str = f"{player.get('assists', 0)}({player.get('assists_per_10', 0):.1f})"
            lines.append(
                "{:<15} {:<12} {:<8} {:<8} {:<8} {:<7.2f} {:<8.0f} {:<8.0f} "
                "{:<10.1f} {:<8.1f}".format(
                    player['name'], hero, kills_str, deaths_str, assists_str,
                    player.get('kdaRatio', 0), player.get('damage_per_10', 0),
                    player.get('healing_per_10', 0),
                    player.get('kill_participation', 0),
                    player.get('accuracy', 0)
                )
            )
        lines.append("")
    return "\n".join(lines)


def generate_player_report(player_name, stats):
    """
    Generate a detailed tournament performance report for a single player.

    Args:
        player_name: The player's in-game name.
        stats: A dictionary containing aggregated stats for the player.

    Returns:
        A formatted string report.
    """
    lines = [
        f"Player: {player_name}",
        f"Matches Played: {stats['matches_played']}",
        f"Record: {stats['wins']}-{stats['losses']} ({stats['win_rate']:.1f}% win rate)",
        f"Heroes Played: {', '.join(stats['heroes_played'])}",
        f"Best Hero: {stats.get('best_hero', 'N/A')} (Best KDA: {stats['best_kda']:.2f})",
        "",
        "Average Stats Per Match:",
        f"KDA: {stats['avg_kda']:.2f}",
        f"K/D/A: {stats['avg_kills']:.1f}/{stats['avg_deaths']:.1f}/{stats['avg_assists']:.1f}",
    ]
    if 'avg_kills_per_10' in stats:
        lines.append(
            f"Per 10 min: {stats['avg_kills_per_10']:.1f}/"
            f"{stats['avg_deaths_per_10']:.1f}/"
            f"{stats['avg_assists_per_10']:.1f}"
        )
    lines.extend([
        f"Final Blows: {stats['avg_last_kills']:.2f}",
        f"Solo Kills: {stats['avg_solo_kills']:.2f}",
        f"Damage: {stats['avg_damage']:.0f}",
        f"Healing: {stats['avg_healing']:.0f}",
        f"Average Kill Participation: {stats.get('avg_kill_participation', 0):.1f}%",
        "",
        "Match Performances:"
    ])
    header = (
        "{:<10} {:<12} {:<6} {:<12} {:<12} {:<7} {:<8} {:<8} {:<10}"
    ).format("Match", "Hero", "Result", "K/D/A", "Per 10min", "KDA",
              "DMG/10", "HEAL/10", "K.Part%")
    lines.append(header)
    lines.append("-" * len(header))
    for perf in stats.get('match_performances', []):
        special = " (MVP)" if perf.get('is_mvp') else " (SVP)" if perf.get('is_svp') else ""
        match_num = perf['match_id'].split('_')[-1]
        kda_str = f"{perf['kills']}/{perf['deaths']}/{perf['assists']}"
        per_10_str = (
            f"{perf['kills_per_10']:.1f}/{perf['deaths_per_10']:.1f}/{perf['assists_per_10']:.1f}"
            if 'kills_per_10' in perf else ""
        )
        lines.append(
            "{:<10} {:<12} {:<6} {:<12} {:<12} {:<7.2f} {:<8.0f} {:<8.0f} "
            "{:<10.1f}{}".format(
                match_num, perf['hero'], perf['result'].upper(), kda_str,
                per_10_str, perf['kda'], perf.get('damage_per_10', 0),
                perf.get('healing_per_10', 0), perf.get('kill_participation', 0),
                special
            )
        )
    return "\n".join(lines)


def generate_tournament_report(tournament, player_ign, detailed=True):
    """
    Generate a comprehensive tournament report.

    This includes an overall summary, a performance summary for teammates,
    and (if detailed=True) detailed match and player reports.
    """
    lines = []
    start_date = tournament['start_date'].strftime('%Y-%m-%d')
    end_date = tournament['end_date'].strftime('%Y-%m-%d')
    lines.append(f"Tournament #{tournament['id']}")
    if start_date == end_date:
        lines.append(f"Date: {start_date}")
    else:
        lines.append(f"Date Range: {start_date} to {end_date}")
    lines.append(f"Matches: {tournament['match_count']}")
    lines.append("")

    team_wins = sum(1 for m in tournament['matches'] if m['result'] == 'win')
    team_losses = tournament['match_count'] - team_wins
    lines.append(f"Team Record: {team_wins}-{team_losses}")
    lines.append("")

    # Identify teammates from matches.
    teammates = set()
    for match in tournament['matches']:
        team_id = next(
            (p['team_id'] for p in match['team_stats']
             if p['name'] == player_ign), None
        )
        if team_id is not None:
            teammates.update(
                p['name'] for p in match['team_stats'] if p['team_id'] == team_id
            )

    lines.append("Team Performance Summary:")
    header = (
        "{:<15} {:<12} {:<8} {:<8} {:<12} {:<12} {:<8}"
    ).format("Player", "Record", "WinRate", "BestHero", "BestKDA",
              "K/D/A", "AvgDMG")
    lines.append(header)
    lines.append("-" * len(header))
    for pname, stats in tournament['player_stats'].items():
        if pname in teammates:
            record = f"{stats['wins']}-{stats['losses']}"
            kd_a = f"{stats['avg_kills']:.1f}/{stats['avg_deaths']:.1f}/{stats['avg_assists']:.1f}"
            lines.append(
                "{:<15} {:<12} {:<8.1f} {:<8} {:<12.2f} {:<12} {:<8.0f}".format(
                    pname, record, stats['win_rate'],
                    stats.get('best_hero', 'N/A'),
                    stats['best_kda'], kd_a, stats['avg_damage']
                )
            )
    lines.append("")
    lines.append("Match Results:")
    for i, match in enumerate(tournament['matches']):
        lines.append(
            f"Match {i + 1}: {match['map']} ({match['mode']}) - "
            f"{match['result'].upper()} ({match['score']})"
        )
    lines.append("")
    if detailed:
        lines.append("=== DETAILED MATCH REPORTS ===")
        for i, match in enumerate(tournament['matches']):
            lines.append("")
            lines.append(f"--- MATCH {i + 1} DETAILS ---")
            lines.append(generate_match_report(match, filter_teammates=True,
                                               player_ign=player_ign))
        lines.append("=== DETAILED PLAYER REPORTS ===")
        for pname, stats in tournament['player_stats'].items():
            if pname in teammates:
                lines.append("")
                lines.append(f"--- {pname.upper()} TOURNAMENT PERFORMANCE ---")
                lines.append(generate_player_report(pname, stats))
    return "\n".join(lines)


def analyze_tournaments(data, detailed=True, cache_dir=None):
    """
    Analyze tournament data for a player and aggregate stats.

    Uses detailed match data if available (with caching support).
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
        driver = webdriver.Chrome(options=create_chrome_options())
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
                        stats['avg_damage_per_10'] = (stats['total_damage'] / total_min) * 10
                        stats['avg_healing_per_10'] = (stats['total_healing'] / total_min) * 10
                        stats['avg_damage_taken_per_10'] = (stats['total_damage_taken'] / total_min) * 10
                    stats['heroes_played'] = list(stats['heroes_played'])
                    stats['win_rate'] = (stats['wins'] / stats['matches_played']) * 100

            tournament_results.append(tournament_info)
        return tournament_results
    finally:
        if driver:
            driver.quit()


def main():
    parser = argparse.ArgumentParser(
        description="Analyze Marvel Rivals tournament data"
    )
    parser.add_argument('file', help='Path to the player data JSON file')
    parser.add_argument('--cache', help='Directory to cache detailed match data',
                        default='match_cache')
    parser.add_argument('--output', help='Directory to save reports',
                        default='tournament_reports')
    args = parser.parse_args()

    if not os.path.exists(args.output):
        os.makedirs(args.output)

    print(f"Loading data from {args.file}")
    data = load_data(args.file)
    player_ign = data.get('ign', 'unknown_player')
    print(f"Analyzing tournaments for player {player_ign}")
    tournaments = analyze_tournaments(data, detailed=True, cache_dir=args.cache)

    print("Generating tournament reports")
    for tournament in tournaments:
        report = generate_tournament_report(tournament, player_ign, detailed=True)
        report_filename = os.path.join(args.output, f"{player_ign}_tournament_{tournament['id']}.txt")
        with open(report_filename, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"Report saved to {report_filename}")

    print("\nTournament Summary:")
    for tournament in tournaments:
        wins = sum(1 for m in tournament['matches'] if m['result'] == 'win')
        losses = tournament['match_count'] - wins
        date_str = tournament['start_date'].strftime('%Y-%m-%d')
        if date_str != tournament['end_date'].strftime('%Y-%m-%d'):
            date_str = f"{date_str} to {tournament['end_date'].strftime('%Y-%m-%d')}"
        print(
            f"Tournament #{tournament['id']} - {date_str}: "
            f"{wins}-{losses} ({tournament['match_count']} matches)"
        )


if __name__ == "__main__":
    main()
