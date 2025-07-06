# =============================================================================
# Dockerfile
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

###############################################################################
# Get the base Linux image
FROM python:3.13.5-slim

###############################################################################
# Set workdir
WORKDIR /app

###############################################################################
# Use a minimal, non-root user for security
RUN adduser --disabled-password --gecos '' appuser && chown -R appuser /app
USER appuser

###############################################################################
# Install dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

###############################################################################
# Copy script and player file
COPY fortnite_to_influx.py ./
COPY player.txt ./

###############################################################################
# Set environment variables for InfluxDB and Fortnite API (can be overridden in K8s)
ENV PLAYER_FILE=player.txt
ENV FORTNITE_API_USER_URL=https://fortniteapi.io/v1/lookup
ENV FORTNITE_API_STATS_URL=https://fortniteapi.io/v1/stats
ENV SEASONS_API_URL=https://fortniteapi.io/v1/seasons/list

###############################################################################
# Entrypoint
ENTRYPOINT ["python", "fortnite_to_influx.py"]

###############################################################################
#EOF
