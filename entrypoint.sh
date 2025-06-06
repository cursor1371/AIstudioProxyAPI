#!/bin/sh
set -e
if [ -n "$AUTH_JSON_CONTENT" ]; then
  echo "Found AUTH_JSON_CONTENT. Creating authentication file..."
  mkdir -p /app/auth_profiles/active
  chown appuser:appgroup /app/auth_profiles/active
  echo "$AUTH_JSON_CONTENT" > /app/auth_profiles/active/auth.json
  chown appuser:appgroup /app/auth_profiles/active/auth.json
  echo "Authentication file created at /app/auth_profiles/active/auth.json"
else
  echo "Warning: AUTH_JSON_CONTENT environment variable not set. Running without it."
fi
exec "$@"