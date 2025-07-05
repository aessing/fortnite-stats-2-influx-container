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

"""
Synchronizes Fortnite player statistics and season data into InfluxDB.

This script fetches data from the Fortnite API and stores it in InfluxDB,
avoiding unnecessary writes by comparing with existing data.

Environment Variables:
  FORTNITE_API_USER_URL: URL for Fortnite user lookup API
    Example: https://fortniteapi.io/v1/lookup
  
  FORTNITE_API_STATS_URL: URL for Fortnite player stats API
    Example: https://fortniteapi.io/v1/stats
  
  FORTNITE_API_TOKEN: Authentication token for Fortnite API
    Get from: https://fortniteapi.io/
  
  SEASONS_API_URL: URL for Fortnite seasons data API
    Example: https://fortniteapi.io/v1/seasons/list
  
  INFLUXDB_URL: InfluxDB server URL
    Example: http://localhost:8086
  
  INFLUXDB_TOKEN: InfluxDB authentication token
    Generate from InfluxDB UI or CLI
  
  INFLUXDB_ORG: InfluxDB organization name
    Example: my-org
  
  INFLUXDB_BUCKET: InfluxDB bucket name for data storage
    Example: fortnite-stats
  
  PLAYER_FILE: Path to file containing player names (one per line)
    Example: /config/players.txt
  
  LOG_LEVEL: (Optional) Logging level
    Default: INFO
    Options: DEBUG, INFO, WARNING, ERROR

Usage:
  Set all required environment variables and run:
  $ python fortnite_to_influx.py

Data Storage:
  - Season data is stored in measurement "fortnite_seasons"
  - Player stats are stored in measurement "player_stats"
  - Both use timestamps for versioning
"""

import os
import sys
import time
import logging
import textwrap
from typing import Dict, List, Optional, Any

import requests
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS


# =============================================================================
# CONFIGURATION
# =============================================================================

class Config:
  """Configuration container for all environment variables."""
  
  def __init__(self):
    self.api_user_url = os.getenv('FORTNITE_API_USER_URL')
    self.api_stats_url = os.getenv('FORTNITE_API_STATS_URL')
    self.api_token = os.getenv('FORTNITE_API_TOKEN')
    self.seasons_url = os.getenv('SEASONS_API_URL')
    
    self.influx_url = os.getenv('INFLUXDB_URL')
    self.influx_token = os.getenv('INFLUXDB_TOKEN')
    self.influx_org = os.getenv('INFLUXDB_ORG')
    self.influx_bucket = os.getenv('INFLUXDB_BUCKET')
    
    self.player_file = os.getenv('PLAYER_FILE')
  
  def validate(self) -> bool:
    """Check if all required configuration is present."""
    required = {
      'FORTNITE_API_USER_URL': self.api_user_url,
      'FORTNITE_API_STATS_URL': self.api_stats_url,
      'FORTNITE_API_TOKEN': self.api_token,
      'SEASONS_API_URL': self.seasons_url,
      'INFLUXDB_URL': self.influx_url,
      'INFLUXDB_TOKEN': self.influx_token,
      'INFLUXDB_ORG': self.influx_org,
      'INFLUXDB_BUCKET': self.influx_bucket,
      'PLAYER_FILE': self.player_file
    }
    
    missing = [name for name, value in required.items() if not value]
    
    if missing:
      logger.error(f"Missing environment variables: {', '.join(missing)}")
      user_logger.error(f"❌ Missing environment variables: {', '.join(missing)}")
      return False
      
    if not os.path.exists(self.player_file):
      logger.error(f"Player file not found: {self.player_file}")
      user_logger.error(f"❌ Player file not found: {self.player_file}")
      return False
      
    return True


# =============================================================================
# LOGGING SETUP
# =============================================================================

# Use environment variable to control log level
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()

logging.basicConfig(
  level=getattr(logging, log_level, logging.INFO),
  format='%(asctime)s - %(levelname)s - %(message)s' if log_level == 'DEBUG' else '%(message)s'
)
logger = logging.getLogger(__name__)

# Create a console handler for user-friendly output
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(message)s')
console_handler.setFormatter(console_formatter)

# Add handler for user messages
user_logger = logging.getLogger(f"{__name__}.user")
user_logger.addHandler(console_handler)
user_logger.propagate = False


# =============================================================================
# CONSTANTS
# =============================================================================

# Time ranges for InfluxDB queries
ACCOUNT_ID_LOOKBACK = "-5y"  # How far back to look for account IDs
PLAYER_STATS_LOOKBACK = "-30d"  # How far back to look for recent player stats
SEASON_DATA_LOOKBACK = "-5y"  # How far back to look for season data


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def print_header(title: str, emoji: str = "") -> None:
  """Print a formatted section header."""
  user_logger.info("\n" + "="*60)
  user_logger.info(f"{emoji}  {title}")
  user_logger.info("="*60 + "\n")


def escape_flux_string(value: str) -> str:
  """Escape special characters for Flux queries."""
  if not value:
    return value
  return value.replace('\\', '\\\\').replace('"', '\\"')


def flatten_json(data: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
  """Flatten nested JSON structure for InfluxDB."""
  result = {}
  
  for key, value in data.items():
    new_key = f"{prefix}{key}" if prefix else key
    
    if isinstance(value, dict):
      result.update(flatten_json(value, f"{new_key}_"))
    elif isinstance(value, list):
      for i, item in enumerate(value):
        if isinstance(item, dict):
          result.update(flatten_json(item, f"{new_key}_{i}_"))
        else:
          result[f"{new_key}_{i}"] = item
    else:
      result[new_key] = value
      
  return result


# =============================================================================
# FORTNITE API CLIENT
# =============================================================================

class FortniteAPI:
  """Client for interacting with the Fortnite API."""
  def __init__(self, config: Config):
    self.config = config
    self.headers = {'Authorization': config.api_token}
    self.timeout = 10
  
  def get_account_id(self, player_name: str) -> Optional[str]:
    """Lookup account ID for a player name."""
    try:
      logger.debug(f"Looking up account ID for player: {player_name}")
      logger.debug(f"API URL: {self.config.api_user_url}")
      logger.debug(f"Headers: {self.headers}")
      
      response = requests.get(
        self.config.api_user_url,
        headers=self.headers,
        params={'username': player_name, 'strict': 'true'},
        timeout=self.timeout
      )
      
      logger.debug(f"Response status: {response.status_code}")
      logger.debug(f"Response headers: {dict(response.headers)}")
      
      if response.status_code == 200:
        data = response.json()
        logger.debug(f"API response: {data}")
        
        # Try different possible response structures
        # Option 1: account_id at root level
        if 'account_id' in data:
          logger.info(f"Found account_id at root for {player_name}")
          return data['account_id']
        
        # Option 2: account_id inside result object
        if data.get('result'):
          result = data['result']
          if isinstance(result, dict) and 'account_id' in result:
            logger.info(f"Found account_id in result object for {player_name}")
            return result['account_id']
        
        # Option 3: account object with id field
        if 'account' in data:
          account = data['account']
          if isinstance(account, dict) and 'id' in account:
            logger.info(f"Found id in account object for {player_name}")
            return account['id']
        
        # Log the full response structure to understand what we're getting
        logger.warning(f"Could not find account_id in response for {player_name}. Response: {data}")
      else:
        logger.error(f"API error {response.status_code} for {player_name}")
        logger.error(f"Response body: {response.text}")
        
    except Exception as e:
      logger.exception(f"Failed to get account ID for {player_name}: {e}")
      
    return None
  
  def get_stats(self, account_id: str, max_retries: int = 3) -> Optional[Dict]:
    """Fetch player statistics with retry logic."""
    for attempt in range(max_retries):
      try:
        response = requests.get(
          self.config.api_stats_url,
          headers=self.headers,
          params={'account': account_id},
          timeout=self.timeout
        )
        
        if response.status_code == 200:
          return response.json()
        elif response.status_code == 429:
          # Handle rate limiting
          wait_time = int(response.headers.get('Retry-After', 60))
          logger.warning(f"Rate limited, waiting {wait_time}s")
          user_logger.info(f"⏳ Rate limited. Waiting {wait_time}s...")
          time.sleep(wait_time)
        else:
          # Log the error but don't return - let it retry
          logger.error(f"API error {response.status_code} on attempt {attempt + 1}")
          
      except requests.exceptions.Timeout:
        logger.warning(f"Timeout on attempt {attempt + 1}")
      except Exception as e:
        logger.exception(f"Error on attempt {attempt + 1}: {e}")
      
      # Wait before next retry (except for the last attempt)
      if attempt < max_retries - 1:
        time.sleep(2 ** attempt)  # Exponential backoff
    
    # Only return None after all retries are exhausted
    logger.error(f"Failed to get stats after {max_retries} attempts")
    return None
  
  def get_seasons(self) -> List[Dict]:
    """Fetch all Fortnite seasons."""
    try:
      response = requests.get(
        self.config.seasons_url,
        headers=self.headers,
        params={'lang': 'en'},
        timeout=self.timeout
      )
      
      if response.status_code == 200:
        return response.json().get('seasons', [])
        
    except Exception as e:
      logger.exception(f"Failed to get seasons: {e}")
      
    return []


# =============================================================================
# INFLUXDB CLIENT
# =============================================================================

class InfluxDBStore:
  """Client for interacting with InfluxDB."""
  
  def __init__(self, config: Config):
    self.config = config
    self.client = InfluxDBClient(
      url=config.influx_url,
      token=config.influx_token,
      org=config.influx_org
    )
    self.write_api = self.client.write_api(write_options=SYNCHRONOUS)
    self.query_api = self.client.query_api()
  
  def close(self):
    """Close the InfluxDB connection."""
    self.client.close()
  
  def get_stored_account_id(self, player_name: str) -> Optional[str]:
    """Get account ID from InfluxDB if previously stored."""
    escaped = escape_flux_string(player_name)
    query = textwrap.dedent(f'''
      from(bucket: "{self.config.influx_bucket}")
      |> range(start: {ACCOUNT_ID_LOOKBACK})
      |> filter(fn: (r) => r["_measurement"] == "player_stats" 
                        and r["player"] == "{escaped}")
      |> last()
    ''').strip()
    
    try:
      tables = self.query_api.query(query, org=self.config.influx_org)
      for table in tables:
        for record in table.records:
          if record.get_field() == "account_id":
            return record.get_value()
    except Exception as e:
      logger.debug(f"Error querying account ID: {e}")
      
    return None
  
  def get_last_player_stats(self, player_name: str) -> Optional[Dict]:
    """Get the most recent stats for a player."""
    escaped = escape_flux_string(player_name)
    query = textwrap.dedent(f'''
      from(bucket: "{self.config.influx_bucket}")
      |> range(start: {PLAYER_STATS_LOOKBACK})
      |> filter(fn: (r) => r["_measurement"] == "player_stats" 
                        and r["player"] == "{escaped}")
      |> last()
    ''').strip()
    
    return self._query_to_dict(query)
  
  def get_last_season_data(self, season_id: str) -> Optional[Dict]:
    """Get the most recent data for a season."""
    escaped = escape_flux_string(str(season_id))
    query = textwrap.dedent(f'''
      from(bucket: "{self.config.influx_bucket}")
      |> range(start: {SEASON_DATA_LOOKBACK})
      |> filter(fn: (r) => r["_measurement"] == "fortnite_seasons" 
                        and r["season"] == "{escaped}")
      |> last()
    ''').strip()
    
    return self._query_to_dict(query)
  
  def _query_to_dict(self, query: str) -> Optional[Dict]:
    """Execute query and convert results to dictionary."""
    try:
      tables = self.query_api.query(query, org=self.config.influx_org)
      result = {}
      
      for table in tables:
        for record in table.records:
          field = record.get_field()
          value = record.get_value()
          if field and value is not None:
            result[field] = value
            
      return result if result else None
      
    except Exception as e:
      logger.debug(f"Query error: {e}")
      return None
  
  def write_season(self, season_id: str, data: Dict) -> None:
    """Write season data to InfluxDB."""
    point = Point("fortnite_seasons").tag("season", str(season_id))
    
    for key, value in data.items():
      point = point.field(key, value)
      
    point = point.time(int(time.time() * 1e9), WritePrecision.NS)
    self.write_api.write(
      bucket=self.config.influx_bucket,
      org=self.config.influx_org,
      record=point
    )
  
  def write_player_stats(self, player_name: str, account_id: str, 
                        stats: Dict) -> None:
    """Write player statistics to InfluxDB."""
    point = (Point("player_stats")
            .tag("player", player_name)
            .tag("account_id", account_id))
    
    for key, value in stats.items():
      if isinstance(value, bool):
        point = point.field(key, int(value))
      elif isinstance(value, (int, float)):
        point = point.field(key, float(value))
      elif isinstance(value, str):
        point = point.field(key, value)
        
    point = point.time(int(time.time() * 1e9), WritePrecision.NS)
    self.write_api.write(
      bucket=self.config.influx_bucket,
      org=self.config.influx_org,
      record=point
    )


# =============================================================================
# DATA COMPARISON
# =============================================================================

def has_data_changed(new_data: Dict, old_data: Optional[Dict]) -> bool:
  """Check if data has changed compared to stored version."""
  # Fix: Check explicitly for None instead of falsy
  if old_data is None:
    return True
  
  # Get all keys from both datasets
  all_keys = set(new_data.keys()) | set(old_data.keys())
  
  for key in all_keys:
    new_val = new_data.get(key)
    old_val = old_data.get(key)
    
    # Check if field exists in one but not the other
    if (key in new_data) != (key in old_data):
      return True
      
    # Check if values differ
    if new_val != old_val:
      return True
      
  return False


def is_api_error(stats: Dict) -> bool:
  """Check if the stats indicate an API error."""
  return (
    stats.get("error", "").upper() == "UNKNOWN" or
    (stats.get("_field") == "error" and 
     str(stats.get("_value", "")).upper() == "UNKNOWN")
  )


# =============================================================================
# MAIN SYNCHRONIZATION LOGIC
# =============================================================================

class FortniteSync:
  """Main synchronization orchestrator."""
  
  def __init__(self, config: Config):
    self.config = config
    self.api = FortniteAPI(config)
    self.db = InfluxDBStore(config)
  
  def sync_seasons(self) -> None:
    """Synchronize Fortnite seasons to InfluxDB."""
    print_header("Fortnite Season Sync", "📅")
    
    seasons = self.api.get_seasons()
    updates = 0
    
    for season in seasons:
      season_id = season.get('season')
      if not season_id:
        continue
        
      # Build season data
      season_data = {}
      if season.get('startDate'):
        season_data['start'] = season['startDate']
      if season.get('endDate'):
        season_data['end'] = season['endDate']
        
      # Check if update needed
      stored = self.db.get_last_season_data(season_id)
      if not has_data_changed(season_data, stored):
        logger.debug(f"Season {season_id} unchanged")
        user_logger.info(f"⏩ Season {season_id} unchanged")
        continue
        
      # Write update
      logger.info(f"Updating season {season_id}")
      user_logger.info(f"✅ Updating season {season_id}")
      self.db.write_season(season_id, season_data)
      updates += 1
    
    user_logger.info(f"\n📊 Updated {updates} seasons\n")
  
  def sync_players(self) -> None:
    """Synchronize player statistics to InfluxDB."""
    print_header("Fortnite Player Stats Sync", "🎮")
    
    # Load player list
    with open(self.config.player_file) as f:
      players = [line.strip() for line in f if line.strip()]
    
    for idx, player in enumerate(players, 1):
      self._sync_player(player, idx, len(players))
      time.sleep(1)  # Rate limiting
  
  def _sync_player(self, player: str, idx: int, total: int) -> None:
    """Sync a single player's statistics."""
    user_logger.info(f"\n[{idx:02d}/{total:02d}] Processing: {player}")
    
    # Get account ID
    account_id = self.db.get_stored_account_id(player)
    if account_id:
      logger.debug(f"Found account ID in database for {player}")
      user_logger.info(f"  ✓ Found account ID in database")
    else:
      account_id = self.api.get_account_id(player)
      if not account_id:
        logger.error(f"Failed to get account ID for {player}")
        user_logger.info(f"  ✗ Failed to get account ID")
        return
      logger.info(f"Retrieved account ID from API for {player}")
      user_logger.info(f"  ✓ Retrieved account ID from API")
    
    # Get stats
    stats = self.api.get_stats(account_id)
    if not stats:
      logger.error(f"Failed to get stats for {player}")
      user_logger.info(f"  ✗ Failed to get stats")
      return
    
    # Add metadata
    stats['player'] = player
    stats['account_id'] = account_id
    
    # Flatten for storage
    flat_stats = flatten_json(stats)
    
    # Check for errors
    if is_api_error(flat_stats):
      logger.error(f"API returned error for {player}")
      user_logger.info(f"  ✗ API returned error")
      return
    
    # Check if update needed
    stored = self.db.get_last_player_stats(player)
    if stored:
      # Only compare common fields
      stored_filtered = {k: v for k, v in stored.items() if k in flat_stats}
      if not has_data_changed(flat_stats, stored_filtered):
        logger.debug(f"No changes detected for {player}")
        user_logger.info(f"  ⏩ No changes detected")
        return
    
    # Write update
    self.db.write_player_stats(player, account_id, flat_stats)
    logger.info(f"Stats updated for {player}")
    user_logger.info(f"  ✅ Stats updated")
  
  def run(self) -> None:
    """Run the complete synchronization."""
    try:
      self.sync_seasons()
      self.sync_players()
    finally:
      self.db.close()
      logger.info("Synchronization complete")
      user_logger.info("\n✅ Synchronization complete")


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
  """Main entry point."""
  config = Config()
  
  if not config.validate():
    sys.exit(1)
  
  try:
    sync = FortniteSync(config)
    sync.run()
  except KeyboardInterrupt:
    logger.warning("Process interrupted by user")
    user_logger.info("\n\n⚠️  Process interrupted")
  except Exception as e:
    logger.exception(f"Unexpected error: {e}")
    user_logger.error(f"\n❌ Fatal error: {e}")
    sys.exit(1)


if __name__ == "__main__":
  main()
