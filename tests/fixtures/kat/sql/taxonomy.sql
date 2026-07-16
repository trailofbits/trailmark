CREATE SCHEMA audit;
CREATE TABLE audit.events (id bigint, payload text);
CREATE VIEW audit.recent_events AS SELECT id FROM audit.events;
CREATE FUNCTION audit.event_count() RETURNS bigint LANGUAGE SQL
AS $$ SELECT count(*) FROM audit.events $$;
