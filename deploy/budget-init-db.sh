#!/bin/sh
set -eu

# The official PostgreSQL entrypoint runs this only on an empty data volume.
# psql variables quote the password as data, never as executable SQL.
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 --set=app_password="$OTW_APP_DATABASE_PASSWORD" <<'SQL'
CREATE ROLE ordertowork LOGIN PASSWORD :'app_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
REVOKE ALL ON DATABASE ordertowork FROM PUBLIC;
GRANT CONNECT ON DATABASE ordertowork TO ordertowork;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO ordertowork;
ALTER DEFAULT PRIVILEGES FOR ROLE otw_migrator IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ordertowork;
ALTER DEFAULT PRIVILEGES FOR ROLE otw_migrator IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO ordertowork;
SQL
