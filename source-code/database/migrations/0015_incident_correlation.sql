-- P06.07: correlating detection candidates into incidents.
--
-- A candidate belongs to at most one incident (UNIQUE candidate_id); the link
-- records *why* (relation) so an operator can see which candidates are the
-- cause, which are independent sources of the same event, and which are
-- consequences (a queue upstream of a blockage). Hypotheses are ranked and
-- re-generated as evidence changes; `incidents.verified_cause` stays NULL until
-- a person verifies one - a hypothesis is never a fact.

ALTER TABLE incidents ADD COLUMN duplicate_of uuid REFERENCES incidents (incident_id);
ALTER TABLE incidents ADD COLUMN evidence_cleared_at timestamptz;
ALTER TABLE incidents ADD COLUMN evidence_sources text[] NOT NULL DEFAULT '{}';
ALTER TABLE incidents ADD CONSTRAINT incidents_not_own_duplicate CHECK (duplicate_of IS NULL OR duplicate_of <> incident_id);

CREATE TABLE incident_candidates (
    incident_id   uuid NOT NULL REFERENCES incidents (incident_id) ON DELETE CASCADE,
    candidate_id  uuid NOT NULL UNIQUE REFERENCES detection_candidates (candidate_id) ON DELETE CASCADE,
    linked_at     timestamptz NOT NULL,
    relation      text NOT NULL CHECK (relation IN ('primary', 'duplicate_source', 'consequence', 'related')),
    PRIMARY KEY (incident_id, candidate_id)
);
CREATE INDEX incident_candidates_incident_idx ON incident_candidates (incident_id);

CREATE TABLE incident_hypotheses (
    incident_id               uuid NOT NULL REFERENCES incidents (incident_id) ON DELETE CASCADE,
    rank                      integer NOT NULL CHECK (rank >= 1),
    hypothesis                text NOT NULL,
    likelihood                double precision NOT NULL CHECK (likelihood >= 0 AND likelihood <= 1),
    supporting_candidate_ids  uuid[] NOT NULL,
    generated_at              timestamptz NOT NULL,
    PRIMARY KEY (incident_id, rank)
);
