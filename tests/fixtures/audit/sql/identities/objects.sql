CREATE SCHEMA app;
CREATE TABLE app.integers (id bigint);
CREATE TABLE app.strings (id bigint);
CREATE FUNCTION app.lookup(x integer) RETURNS bigint LANGUAGE SQL
AS $$ SELECT id FROM app.integers LIMIT 1 $$;
CREATE FUNCTION app.lookup(x text) RETURNS bigint LANGUAGE SQL
AS $$ SELECT id FROM app.strings LIMIT 1 $$;
CREATE TABLE app.shared (id bigint);
CREATE FUNCTION app.shared(x integer) RETURNS bigint LANGUAGE SQL AS $$ SELECT x $$;
CREATE VIEW app.shared_view AS SELECT id FROM app.shared;
