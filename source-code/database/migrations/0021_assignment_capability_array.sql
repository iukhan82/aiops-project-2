-- P08.07: `emergency_unit_assignments.capability` was created as `text`, but the assignment contract (and the repository, which passes a
-- list) treat it as an array of capabilities. psycopg stored the list as the text '{als,transport}', so the API served a string where
-- the contract says an array. Convert the column to the type it always meant to be, keeping every existing value.

ALTER TABLE emergency_unit_assignments
    ALTER COLUMN capability TYPE text[]
    USING string_to_array(trim(both '{}' from capability), ',');
