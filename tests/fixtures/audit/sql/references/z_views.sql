CREATE VIEW app.exact AS SELECT id FROM app.events;
CREATE VIEW app.case_variant AS SELECT id FROM APP.EVENTS;
CREATE VIEW app.cte_shadow AS WITH shadow AS (SELECT 1 AS id) SELECT id FROM shadow;
CREATE VIEW app.quoted_view AS SELECT id FROM app."quoted";
CREATE VIEW app.unquoted_equivalent AS SELECT id FROM app.quoted;
CREATE VIEW app.mixed_exact AS SELECT id FROM app."MiXeD";
CREATE VIEW app.mixed_distinct AS SELECT id FROM app.mixed;
CREATE VIEW app.function_view AS SELECT app.helper(1) AS id;
CREATE FUNCTION app.helper(x integer) RETURNS integer LANGUAGE SQL AS $$ SELECT x $$;
CREATE FUNCTION app.on_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RETURN NEW; END;
$$;
CREATE TRIGGER event_insert BEFORE INSERT ON app.events
FOR EACH ROW EXECUTE FUNCTION app.on_event();
