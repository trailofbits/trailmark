CREATE SCHEMA app;
CREATE TABLE app.events (id bigint, payload text);
CREATE VIEW app.recent AS SELECT id FROM app.events;
CREATE MATERIALIZED VIEW app.snapshot AS SELECT id FROM app.events;
CREATE FUNCTION app.event_count(x integer DEFAULT 1) RETURNS bigint LANGUAGE SQL
AS $$ SELECT count(*) FROM app.events $$;
CREATE PROCEDURE app.refresh(IN p_limit integer DEFAULT 10) LANGUAGE SQL
AS $$ SELECT id FROM app.events LIMIT p_limit $$;
