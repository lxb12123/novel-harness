-- M4 proposal reviews commit Canon before their append-only decision log.  The proposal row is
-- therefore the durable outbox: it must retain the exact action, resulting version, and immutable
-- audit material needed to close that narrow post-commit gap without re-reading mutable facts.

ALTER TABLE proposal_set ADD COLUMN resolution_action TEXT
  CHECK (
    resolution_action IS NULL
    OR resolution_action IN ('accept','reject','edit','bystander')
  );
ALTER TABLE proposal_set ADD COLUMN resolved_canon_version INTEGER
  CHECK (resolved_canon_version IS NULL OR resolved_canon_version >= 0);
ALTER TABLE proposal_set ADD COLUMN audit_envelope_json TEXT
  CHECK (audit_envelope_json IS NULL OR json_valid(audit_envelope_json));

-- Backfill terminal v2 rows only when an attached proposal-review decision makes the historical
-- action unambiguous.  A terminal row with no decision remains visibly incomplete; recovery code
-- refuses it instead of inventing reject vs bystander history.
UPDATE proposal_set
SET resolution_action = (
      SELECT json_extract(decision_log.payload_json, '$.action')
      FROM decision_log WHERE decision_log.id = proposal_set.decision_log_id
    ),
    resolved_canon_version = (
      SELECT json_extract(decision_log.payload_json, '$.canon_version')
      FROM decision_log WHERE decision_log.id = proposal_set.decision_log_id
    ),
    audit_envelope_json = (
      SELECT json_object(
        'payload', json(decision_log.payload_json),
        'subject_name', decision_log.subject_name,
        'quote_text', decision_log.quote_text,
        'quote_sha256', decision_log.quote_sha256,
        'chapter_number', decision_log.chapter_number,
        'para_index', decision_log.para_index
      )
      FROM decision_log WHERE decision_log.id = proposal_set.decision_log_id
    )
WHERE status <> 'PENDING'
  AND decision_log_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM decision_log
    WHERE decision_log.id = proposal_set.decision_log_id
      AND decision_log.project_id = proposal_set.project_id
      AND decision_log.kind = 'proposal_review'
      AND json_extract(decision_log.payload_json, '$.proposal_id') = proposal_set.id
      AND json_extract(decision_log.payload_json, '$.status') = proposal_set.status
      AND json_extract(decision_log.payload_json, '$.kind') = proposal_set.kind
      AND (
        (proposal_set.status = 'ACCEPTED'
          AND json_extract(decision_log.payload_json, '$.action') = 'accept'
          AND json_extract(decision_log.payload_json, '$.canon_version')
              = proposal_set.base_canon_version + 1)
        OR (proposal_set.status = 'EDITED'
          AND json_extract(decision_log.payload_json, '$.action') = 'edit'
          AND json_extract(decision_log.payload_json, '$.canon_version')
              = proposal_set.base_canon_version + 1)
        OR (proposal_set.status = 'REJECTED'
          AND json_extract(decision_log.payload_json, '$.action') IN ('reject','bystander')
          AND json_extract(decision_log.payload_json, '$.canon_version')
              = proposal_set.base_canon_version)
      )
  );

-- One immutable decision can close only one proposal outbox row.
CREATE UNIQUE INDEX idx_proposal_set_decision_once
  ON proposal_set(decision_log_id)
  WHERE decision_log_id IS NOT NULL;

-- JSON expression lookup keeps recovery bounded.  The trigger below supplies future uniqueness
-- without making an old database containing duplicate bad history impossible to migrate.
CREATE INDEX idx_decision_log_proposal_review
  ON decision_log(project_id, json_extract(payload_json, '$.proposal_id'))
  WHERE kind = 'proposal_review'
    AND json_type(payload_json, '$.proposal_id') = 'text';

CREATE TRIGGER decision_log_one_proposal_review_insert
BEFORE INSERT ON decision_log
WHEN NEW.kind = 'proposal_review'
  AND json_type(NEW.payload_json, '$.proposal_id') = 'text'
BEGIN
  SELECT RAISE(ABORT, 'proposal review decision already exists')
  WHERE EXISTS (
    SELECT 1 FROM decision_log
    WHERE project_id = NEW.project_id
      AND kind = 'proposal_review'
      AND json_type(payload_json, '$.proposal_id') = 'text'
      AND json_extract(payload_json, '$.proposal_id')
          = json_extract(NEW.payload_json, '$.proposal_id')
  );
END;

-- New writes must make the proposal row a complete, self-consistent outbox record.
CREATE TRIGGER proposal_resolution_metadata_insert
BEFORE INSERT ON proposal_set
WHEN NOT (
  (
    NEW.status = 'PENDING'
    AND NEW.resolution_action IS NULL
    AND NEW.resolved_canon_version IS NULL
    AND NEW.audit_envelope_json IS NULL
    AND NEW.decision_log_id IS NULL
  )
  OR (
    NEW.status <> 'PENDING'
    AND NEW.resolution_action IS NOT NULL
    AND NEW.resolved_canon_version IS NOT NULL
    AND NEW.audit_envelope_json IS NOT NULL
    AND json_type(NEW.audit_envelope_json, '$.payload') = 'object'
    AND json_extract(NEW.audit_envelope_json, '$.payload.proposal_id') = NEW.id
    AND json_extract(NEW.audit_envelope_json, '$.payload.action') = NEW.resolution_action
    AND json_extract(NEW.audit_envelope_json, '$.payload.status') = NEW.status
    AND json_extract(NEW.audit_envelope_json, '$.payload.kind') = NEW.kind
    AND json_extract(NEW.audit_envelope_json, '$.payload.canon_version')
        = NEW.resolved_canon_version
    AND (
      (NEW.status = 'ACCEPTED' AND NEW.resolution_action = 'accept'
        AND NEW.resolved_canon_version = NEW.base_canon_version + 1)
      OR (NEW.status = 'EDITED' AND NEW.resolution_action = 'edit'
        AND NEW.resolved_canon_version = NEW.base_canon_version + 1)
      OR (NEW.status = 'REJECTED' AND NEW.resolution_action IN ('reject','bystander')
        AND NEW.resolved_canon_version = NEW.base_canon_version)
    )
    AND (NEW.resolution_action <> 'bystander' OR NEW.kind = 'new_character')
  )
)
BEGIN
  SELECT RAISE(ABORT, 'proposal resolution metadata is incomplete or incoherent');
END;

CREATE TRIGGER proposal_resolution_metadata_update
BEFORE UPDATE OF status, resolution_action, resolved_canon_version,
                 audit_envelope_json, decision_log_id, base_canon_version, kind
ON proposal_set
WHEN NOT (
  (
    NEW.status = 'PENDING'
    AND NEW.resolution_action IS NULL
    AND NEW.resolved_canon_version IS NULL
    AND NEW.audit_envelope_json IS NULL
    AND NEW.decision_log_id IS NULL
  )
  OR (
    NEW.status <> 'PENDING'
    AND NEW.resolution_action IS NOT NULL
    AND NEW.resolved_canon_version IS NOT NULL
    AND NEW.audit_envelope_json IS NOT NULL
    AND json_type(NEW.audit_envelope_json, '$.payload') = 'object'
    AND json_extract(NEW.audit_envelope_json, '$.payload.proposal_id') = NEW.id
    AND json_extract(NEW.audit_envelope_json, '$.payload.action') = NEW.resolution_action
    AND json_extract(NEW.audit_envelope_json, '$.payload.status') = NEW.status
    AND json_extract(NEW.audit_envelope_json, '$.payload.kind') = NEW.kind
    AND json_extract(NEW.audit_envelope_json, '$.payload.canon_version')
        = NEW.resolved_canon_version
    AND (
      (NEW.status = 'ACCEPTED' AND NEW.resolution_action = 'accept'
        AND NEW.resolved_canon_version = NEW.base_canon_version + 1)
      OR (NEW.status = 'EDITED' AND NEW.resolution_action = 'edit'
        AND NEW.resolved_canon_version = NEW.base_canon_version + 1)
      OR (NEW.status = 'REJECTED' AND NEW.resolution_action IN ('reject','bystander')
        AND NEW.resolved_canon_version = NEW.base_canon_version)
    )
    AND (NEW.resolution_action <> 'bystander' OR NEW.kind = 'new_character')
  )
)
BEGIN
  SELECT RAISE(ABORT, 'proposal resolution metadata is incomplete or incoherent');
END;

CREATE TRIGGER proposal_resolution_metadata_immutable
BEFORE UPDATE OF status, resolution_action, resolved_canon_version,
                 audit_envelope_json, base_canon_version, kind
ON proposal_set
WHEN OLD.status <> 'PENDING'
  AND (
    NEW.status IS NOT OLD.status
    OR NEW.resolution_action IS NOT OLD.resolution_action
    OR NEW.resolved_canon_version IS NOT OLD.resolved_canon_version
    OR NEW.audit_envelope_json IS NOT OLD.audit_envelope_json
    OR NEW.base_canon_version IS NOT OLD.base_canon_version
    OR NEW.kind IS NOT OLD.kind
  )
BEGIN
  SELECT RAISE(ABORT, 'terminal proposal resolution metadata is immutable');
END;

CREATE TRIGGER proposal_decision_attachment_matches
BEFORE UPDATE OF decision_log_id ON proposal_set
WHEN NEW.decision_log_id IS NOT NULL
  AND NEW.decision_log_id IS NOT OLD.decision_log_id
BEGIN
  SELECT RAISE(ABORT, 'proposal decision does not match durable audit envelope')
  WHERE NOT EXISTS (
    SELECT 1 FROM decision_log
    WHERE decision_log.id = NEW.decision_log_id
      AND decision_log.project_id = NEW.project_id
      AND decision_log.kind = 'proposal_review'
      AND json(decision_log.payload_json)
          = json(json_extract(NEW.audit_envelope_json, '$.payload'))
      AND decision_log.decision = CASE NEW.resolution_action
        WHEN 'accept' THEN 'accept'
        WHEN 'edit' THEN 'edit'
        ELSE 'reject'
      END
      AND decision_log.subject_name
          IS json_extract(NEW.audit_envelope_json, '$.subject_name')
      AND decision_log.quote_text
          IS json_extract(NEW.audit_envelope_json, '$.quote_text')
      AND decision_log.quote_sha256
          IS json_extract(NEW.audit_envelope_json, '$.quote_sha256')
      AND decision_log.chapter_number
          IS json_extract(NEW.audit_envelope_json, '$.chapter_number')
      AND decision_log.para_index
          IS json_extract(NEW.audit_envelope_json, '$.para_index')
  );
END;

CREATE TRIGGER proposal_decision_attachment_immutable
BEFORE UPDATE OF decision_log_id ON proposal_set
WHEN OLD.decision_log_id IS NOT NULL
  AND NEW.decision_log_id IS NOT OLD.decision_log_id
BEGIN
  SELECT RAISE(ABORT, 'proposal decision attachment is immutable');
END;

PRAGMA user_version = 3;
