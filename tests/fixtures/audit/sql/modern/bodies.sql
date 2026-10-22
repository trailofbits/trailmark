CREATE SCHEMA app;
CREATE TABLE app.items (id bigint);
CREATE FUNCTION app.increment(x integer) RETURNS integer LANGUAGE SQL RETURN x + 1;
CREATE FUNCTION app.atomic_count() RETURNS bigint LANGUAGE SQL
BEGIN ATOMIC
SELECT count(*) FROM app.items;
END;
CREATE PROCEDURE app.atomic_insert(x bigint) LANGUAGE SQL
BEGIN ATOMIC
INSERT INTO app.items VALUES (x);
END;
