#!/bin/bash
# Creates the application database alongside the test one.
#
# The postgres image's POSTGRES_DB creates exactly one database, but the stack needs two: tests
# drop and recreate every table, which would destroy a developer's local data if they shared.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-SQL
    SELECT 'CREATE DATABASE agrovision'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'agrovision')\gexec
SQL
