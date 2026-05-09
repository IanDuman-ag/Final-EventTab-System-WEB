-- EventTabs PostgreSQL Database Setup Script
-- Run this script as postgres superuser

-- Create database
CREATE DATABASE eventtabs
    WITH 
    OWNER = postgres
    ENCODING = 'UTF8'
    LC_COLLATE = 'English_United States.1252'
    LC_CTYPE = 'English_United States.1252'
    TABLESPACE = pg_default
    CONNECTION LIMIT = -1;

COMMENT ON DATABASE eventtabs IS 'EventTabs application database';

-- Create user
CREATE USER event_users WITH
    LOGIN
    PASSWORD 'event_pass'
    NOSUPERUSER
    NOCREATEDB
    NOCREATEROLE
    NOREPLICATION
    CONNECTION LIMIT -1;

-- Grant privileges on database
GRANT ALL PRIVILEGES ON DATABASE eventtabs TO event_users;

-- Connect to eventtabs database
\c eventtabs

-- Grant schema privileges
GRANT ALL ON SCHEMA public TO event_users;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO event_users;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO event_users;

-- Set default privileges for future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO event_users;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO event_users;

-- Verify connection
SELECT current_database(), current_user;

-- Show granted privileges
\du event_users
