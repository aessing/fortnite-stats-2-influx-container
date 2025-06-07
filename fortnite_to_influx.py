# =============================================================================
# Fortnite Stats 2 Influx Container
# https://github.com/aessing/fortnite-stats-2-influx-container
# -----------------------------------------------------------------------------
# Developer.......: Andre Essing (https://github.com/aessing)
#                                (https://www.linkedin.com/in/aessing/)
# -----------------------------------------------------------------------------
# THIS CODE AND INFORMATION ARE PROVIDED "AS IS" WITHOUT WARRANTY OF ANY KIND,
# EITHER EXPRESSED OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND/OR FITNESS FOR A PARTICULAR PURPOSE.
# =============================================================================

import os
import requests
import json
import time
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# Load environment variables
API_USER_URL = os.getenv('FORTNITE_API_USER_URL')
API_STATS_URL = os.getenv('FORTNITE_API_STATS_URL')
FORTNITE_API_TOKEN = os.getenv('FORTNITE_API_TOKEN')
INFLUXDB_URL = os.getenv('INFLUXDB_URL')
INFLUXDB_TOKEN = os.getenv('INFLUXDB_TOKEN')
INFLUXDB_ORG = os.getenv('INFLUXDB_ORG')
INFLUXDB_BUCKET = os.getenv('INFLUXDB_BUCKET')
PLAYER_FILE = os.getenv('PLAYER_FILE')
SEASONS_API_URL = os.getenv('SEASONS_API_URL')

# Helper to get account id from player name
def get_account_id(player_name):
    try:
        resp = requests.get(
            API_USER_URL,
            headers={'Authorization': FORTNITE_API_TOKEN},
            params={'username': player_name, 'strict': 'true'}
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get('result'):
                return data.get('account_id')
            else:
                print(f"🟡 [DEBUG] No 'result' in API response for '{player_name}': {json.dumps(data, indent=2)}")
        else:
            print(f"❌ [ERROR] API returned status {resp.status_code} for '{player_name}': {resp.text}")
    except Exception as e:
        print(f"❌ [EXCEPTION] Exception while fetching account id for '{player_name}': {e}")
    return None

# Helper to get account id from InfluxDB
def get_account_id_from_influx(client, player_name):
    query = f'''from(bucket: "{INFLUXDB_BUCKET}")\n  |> range(start: -5y)\n  |> filter(fn: (r) => r["_measurement"] == "player_stats" and r["player"] == "{player_name}")\n  |> last()'''
    try:
        tables = client.query_api().query(query, org=INFLUXDB_ORG)
        if tables:
            for table in tables:
                for record in table.records:
                    if record.get_field() == "account_id" and record.get_value():
                        return record.get_value()
    except Exception as e:
        print(f"🟡 [DEBUG] Exception while querying InfluxDB for account_id of '{player_name}': {e}")
    return None

# Helper to get stats for account id
def get_stats(account_id):
    resp = requests.get(API_STATS_URL, headers={'Authorization': FORTNITE_API_TOKEN}, params={'account': account_id})
    if resp.status_code == 200:
        return resp.json()
    return None

# Helper to get current Fortnite seasons and their start/end times
def get_seasons():
    resp = requests.get(SEASONS_API_URL, headers={'Authorization': FORTNITE_API_TOKEN}, params={'lang': 'en'})
    if resp.status_code == 200:
        return resp.json().get('seasons', [])
    return []

# Helper to get last record for player from InfluxDB
def get_last_record(client, player_name):
    query = f'''from(bucket: "{INFLUXDB_BUCKET}")\n  |> range(start: -30d)\n  |> filter(fn: (r) => r["_measurement"] == "player_stats" and r["player"] == "{player_name}")\n  |> last()'''
    tables = client.query_api().query(query, org=INFLUXDB_ORG)
    if tables:
        user_fields = {}
        for table in tables:
            for record in table.records:
                # Only use fields that are not system fields and are not None
                if record.get_field() and record.get_value() is not None:
                    user_fields[record.get_field()] = record.get_value()
        return user_fields if user_fields else None
    return None

# Helper to compare stats (updated logic)
def stats_changed(new_stats, last_stats):
    if not last_stats:
        return True
    # Only compare fields that exist in both dicts and have non-None values
    compare_keys = set(new_stats.keys()) & set(last_stats.keys())
    filtered_new = {k: new_stats[k] for k in compare_keys if new_stats[k] is not None}
    filtered_last = {k: last_stats[k] for k in compare_keys if last_stats[k] is not None}
    return filtered_new != filtered_last

# Helper to flatten stats for InfluxDB fields
def flatten_stats(stats):
    flat = {}
    def _flatten(obj, prefix=""):
        if isinstance(obj, dict):
            for k, v in obj.items():
                _flatten(v, f"{prefix}{k}_")
        else:
            flat[prefix[:-1]] = obj
    _flatten(stats)
    return flat

# Helper to get last season record from InfluxDB
def get_last_season_record(client, season_id):
    query = f'''from(bucket: "{INFLUXDB_BUCKET}")\n  |> range(start: -5y)\n  |> filter(fn: (r) => r["_measurement"] == "fortnite_seasons" and r["season"] == "{season_id}")\n  |> last()'''
    tables = client.query_api().query(query, org=INFLUXDB_ORG)
    if tables:
        user_fields = {}
        for table in tables:
            for record in table.records:
                if record.get_field() and record.get_value() is not None:
                    user_fields[record.get_field()] = record.get_value()
        return user_fields if user_fields else None
    return None

# Helper to compare season data
def season_changed(new_season, last_season):
    if not last_season:
        return True
    compare_keys = set(new_season.keys()) & set(last_season.keys())
    filtered_new = {k: new_season[k] for k in compare_keys if new_season[k] is not None}
    filtered_last = {k: last_season[k] for k in compare_keys if last_season[k] is not None}
    return filtered_new != filtered_last

# Helper to print headers
def print_header(title, emoji=""): 
    print("\n" + "="*60)
    print(f"{emoji}  {title}")
    print("="*60 + "\n")

# Main logic
def main():
    print_header("Fortnite Season Sync", "📅")
    influx = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    write_api = influx.write_api(write_options=SYNCHRONOUS)

    seasons = get_seasons()
    for season in seasons:
        season_id = season.get('season')
        start = season.get('startDate')
        end = season.get('endDate')
        season_fields = {}
        if start:
            season_fields['start'] = start
        if end:
            season_fields['end'] = end
        last_season = get_last_season_record(influx, season_id)
        if not season_changed(season_fields, last_season):
            print(f"⏩  No change for season {season_id}, skipping write.\n")
            continue
        print(f"✅  Writing season {season_id} to InfluxDB:")
        print(f"    ├─ Start: {start}")
        print(f"    └─ End:   {end}\n")
        point = Point("fortnite_seasons").tag("season", str(season_id))
        for k, v in season_fields.items():
            point = point.field(k, v)
        point = point.time(int(time.time() * 1e9), WritePrecision.NS)
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)

    print_header("Fortnite Player Stats Sync", "🎮")
    with open(PLAYER_FILE) as f:
        players = [line.strip() for line in f if line.strip()]

    for idx, player in enumerate(players, 1):
        print(f"\n{idx:02d}/{len(players):02d} 🔎  Processing: \033[1m{player}\033[0m ...")
        acct_id = get_account_id_from_influx(influx, player)
        if acct_id:
            print(f"   🗃️  Found account_id in InfluxDB: \033[1m{acct_id}\033[0m")
        else:
            acct_id = get_account_id(player)
            if acct_id:
                print(f"   🌐 Queried Fortnite API for account_id: \033[1m{acct_id}\033[0m")
            else:
                print(f"❌  Could not get account id for \033[1m{player}\033[0m (see above for details)\n")
                continue
        stats = get_stats(acct_id)
        if not stats:
            print(f"❌  Could not get stats for \033[1m{player}\033[0m\n")
            continue
        stats['player'] = player
        stats['account_id'] = acct_id  # Add account_id to stats
        flat = flatten_stats(stats)
        last = get_last_record(influx, player)
        if last:
            last_fields = {k: v for k, v in last.items() if k in flat}
        else:
            last_fields = None
        if not stats_changed(flat, last_fields):
            print(f"⏩  No change for \033[1m{player}\033[0m, skipping write.\n")
            continue
        print(f"✅  Writing stats for \033[1m{player}\033[0m to InfluxDB.\n")
        point = Point("player_stats").tag("player", player).tag("account_id", acct_id)  # Add account_id as tag
        for k, v in flat.items():
            if isinstance(v, bool):
                point = point.field(k, int(v))
            elif isinstance(v, (int, float)):
                point = point.field(k, float(v))
            elif isinstance(v, str):
                point = point.field(k, v)
        point = point.time(int(time.time() * 1e9), WritePrecision.NS)
        write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=point)
        print(f"🎉  Wrote stats for \033[1m{player}\033[0m\n")
        time.sleep(1)  # avoid rate limits

if __name__ == "__main__":
    main()
