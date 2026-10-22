CREATE SCHEMA app;
CREATE TABLE app.actual (id bigint);
CREATE TABLE app.decoy (id bigint);
CREATE TABLE app.outside (id bigint);
CREATE PROCEDURE app.refresh(IN p_limit integer DEFAULT 10) LANGUAGE SQL AS $$
SELECT id FROM app.actual LIMIT p_limit;
-- SELECT id FROM app.decoy;
SELECT 'FROM app.literal_decoy';
$$;
SELECT id FROM app.outside;
CREATE FUNCTION app.literal() RETURNS text LANGUAGE SQL
AS $$ SELECT 'CREATE PROCEDURE app.ghost()' $$;
