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
ALTER TABLE proposal_set ADD COLUMN confirmation_fact_kind TEXT
  CHECK (
    confirmation_fact_kind IS NULL
    OR confirmation_fact_kind IN ('event','edge')
  );
ALTER TABLE proposal_set ADD COLUMN confirmation_request_hash TEXT
  CHECK (
    confirmation_request_hash IS NULL
    OR (
      length(confirmation_request_hash) = 64
      AND confirmation_request_hash NOT GLOB '*[^0-9a-f]*'
    )
  );

-- Select a v2 audit decision only when the entire history is unambiguous: exactly one review log
-- names the proposal, its payload/verdict agrees with the terminal row, and an unattached log is
-- not already owned by another proposal.  This also closes the old append-before-attach crash gap.
CREATE TEMP TABLE proposal_audit_candidate_v3 AS
SELECT proposal_set.id AS proposal_id, decision_log.id AS decision_id
FROM proposal_set
JOIN decision_log
  ON decision_log.project_id = proposal_set.project_id
 AND decision_log.kind = 'proposal_review'
 AND json_type(decision_log.payload_json) = 'object'
 AND nh_json_canonical(CAST(decision_log.payload_json AS BLOB)) IS NOT NULL
 AND json_type(decision_log.payload_json, '$.proposal_id') = 'text'
 AND json_extract(decision_log.payload_json, '$.proposal_id') = proposal_set.id
WHERE proposal_set.status <> 'PENDING'
  AND typeof(decision_log.id) = 'text'
  AND nh_utf8_text(CAST(decision_log.id AS BLOB)) = 1
  AND typeof(decision_log.ts) = 'text'
  AND nh_utf8_text(CAST(decision_log.ts AS BLOB)) = 1
  AND typeof(decision_log.project_id) = 'text'
  AND nh_utf8_text(CAST(decision_log.project_id AS BLOB)) = 1
  AND typeof(decision_log.actor) = 'text'
  AND nh_utf8_text(CAST(decision_log.actor AS BLOB)) = 1
  AND (
    decision_log.subject_name IS NULL
    OR (
      typeof(decision_log.subject_name) = 'text'
      AND nh_utf8_text(CAST(decision_log.subject_name AS BLOB)) = 1
    )
  )
  AND (
    (decision_log.quote_text IS NULL AND decision_log.quote_sha256 IS NULL)
    OR (
      typeof(decision_log.quote_text) = 'text'
      AND typeof(decision_log.quote_sha256) = 'text'
      AND decision_log.quote_sha256 = nh_sha256_text(
        CAST(decision_log.quote_text AS BLOB)
      )
    )
  )
  AND (
    decision_log.chapter_number IS NULL
    OR (
      typeof(decision_log.chapter_number) = 'integer'
      AND decision_log.chapter_number >= 1
    )
  )
  AND (
    decision_log.para_index IS NULL
    OR (
      typeof(decision_log.para_index) = 'integer'
      AND decision_log.para_index >= 0
    )
  )
  AND (
    proposal_set.decision_log_id IS NULL
    OR proposal_set.decision_log_id = decision_log.id
  )
  AND json_type(decision_log.payload_json, '$.action') = 'text'
  AND json_type(decision_log.payload_json, '$.status') = 'text'
  AND json_type(decision_log.payload_json, '$.kind') = 'text'
  AND json_type(decision_log.payload_json, '$.canon_version') = 'integer'
  AND (
    SELECT COUNT(*) FROM json_each(decision_log.payload_json)
    WHERE json_each.key = 'proposal_id'
  ) = 1
  AND (
    SELECT COUNT(*) FROM json_each(decision_log.payload_json)
    WHERE json_each.key = 'action'
  ) = 1
  AND (
    SELECT COUNT(*) FROM json_each(decision_log.payload_json)
    WHERE json_each.key = 'status'
  ) = 1
  AND (
    SELECT COUNT(*) FROM json_each(decision_log.payload_json)
    WHERE json_each.key = 'kind'
  ) = 1
  AND (
    SELECT COUNT(*) FROM json_each(decision_log.payload_json)
    WHERE json_each.key = 'canon_version'
  ) = 1
  AND NOT EXISTS (
    SELECT 1
    FROM json_tree(decision_log.payload_json)
    WHERE json_tree.key IS NOT NULL
    GROUP BY json_tree.parent, json_tree.key
    HAVING COUNT(*) > 1
  )
  AND NOT EXISTS (
    SELECT 1
    FROM json_tree(decision_log.payload_json)
    WHERE typeof(json_tree.key) = 'text'
      AND instr(json_tree.fullkey, char(92)) > 0
  )
  AND json_extract(decision_log.payload_json, '$.status') = proposal_set.status
  AND json_extract(decision_log.payload_json, '$.kind') = proposal_set.kind
  AND (
    (proposal_set.status = 'ACCEPTED'
      AND json_extract(decision_log.payload_json, '$.action') = 'accept'
      AND decision_log.decision = 'accept'
      AND json_extract(decision_log.payload_json, '$.canon_version')
          = proposal_set.base_canon_version + 1)
    OR (proposal_set.status = 'EDITED'
      AND json_extract(decision_log.payload_json, '$.action') = 'edit'
      AND decision_log.decision = 'edit'
      AND json_extract(decision_log.payload_json, '$.canon_version')
          = proposal_set.base_canon_version + 1)
    OR (proposal_set.status = 'REJECTED'
      AND json_extract(decision_log.payload_json, '$.action') IN ('reject','bystander')
      AND decision_log.decision = 'reject'
      AND json_extract(decision_log.payload_json, '$.canon_version')
          = proposal_set.base_canon_version)
  )
  AND (
    json_extract(decision_log.payload_json, '$.action') <> 'bystander'
    OR proposal_set.kind = 'new_character'
  )
  AND NOT EXISTS (
    SELECT 1
    FROM decision_log AS duplicate
    WHERE duplicate.project_id = proposal_set.project_id
      AND (
        duplicate.kind = 'proposal_review'
        OR (
          typeof(duplicate.kind) = 'blob'
          AND nh_utf8_text(CAST(duplicate.kind AS BLOB)) = 1
          AND CAST(duplicate.kind AS TEXT) = 'proposal_review'
        )
      )
      AND duplicate.id <> decision_log.id
      AND EXISTS (
        SELECT 1 FROM json_each(duplicate.payload_json)
        WHERE json_each.key = 'proposal_id'
          AND json_each.type = 'text'
          AND json_each.value = proposal_set.id
      )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM proposal_set AS owner
    WHERE owner.decision_log_id = decision_log.id
      AND owner.id <> proposal_set.id
  );

UPDATE proposal_set
SET decision_log_id = (
  SELECT decision_id
  FROM proposal_audit_candidate_v3
  WHERE proposal_id = proposal_set.id
)
WHERE decision_log_id IS NULL
  AND EXISTS (
    SELECT 1 FROM proposal_audit_candidate_v3
    WHERE proposal_id = proposal_set.id
  );

UPDATE proposal_set
SET resolution_action = (
      SELECT json_extract(decision_log.payload_json, '$.action')
      FROM decision_log
      JOIN proposal_audit_candidate_v3
        ON proposal_audit_candidate_v3.decision_id = decision_log.id
      WHERE proposal_audit_candidate_v3.proposal_id = proposal_set.id
    ),
    resolved_canon_version = (
      SELECT json_extract(decision_log.payload_json, '$.canon_version')
      FROM decision_log
      JOIN proposal_audit_candidate_v3
        ON proposal_audit_candidate_v3.decision_id = decision_log.id
      WHERE proposal_audit_candidate_v3.proposal_id = proposal_set.id
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
      FROM decision_log
      JOIN proposal_audit_candidate_v3
        ON proposal_audit_candidate_v3.decision_id = decision_log.id
      WHERE proposal_audit_candidate_v3.proposal_id = proposal_set.id
    )
WHERE EXISTS (
  SELECT 1 FROM proposal_audit_candidate_v3
  WHERE proposal_id = proposal_set.id
);

DROP TABLE proposal_audit_candidate_v3;

-- Index old attachments without auditing them through a UNIQUE build: v2's public store allowed
-- one decision to be shared, and that bad history must migrate into quarantine instead of making
-- the application unable to start.  The triggers reject every future reuse.
CREATE INDEX idx_proposal_set_decision_once
  ON proposal_set(decision_log_id)
  WHERE decision_log_id IS NOT NULL;

CREATE UNIQUE INDEX idx_provisional_confirmation_request
  ON proposal_set(project_id, confirmation_fact_kind, confirmation_request_hash)
  WHERE kind = 'provisional_confirm';

CREATE TRIGGER proposal_decision_once_insert
BEFORE INSERT ON proposal_set
WHEN NEW.decision_log_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'proposal decision is already attached')
  WHERE EXISTS (
    SELECT 1 FROM proposal_set
    WHERE decision_log_id = NEW.decision_log_id
  );
END;

CREATE TRIGGER proposal_decision_once_update
BEFORE UPDATE OF decision_log_id ON proposal_set
WHEN NEW.decision_log_id IS NOT NULL
  AND NEW.decision_log_id IS NOT OLD.decision_log_id
BEGIN
  SELECT RAISE(ABORT, 'proposal decision is already attached')
  WHERE EXISTS (
    SELECT 1 FROM proposal_set
    WHERE decision_log_id = NEW.decision_log_id
      AND id <> NEW.id
  );
END;

-- JSON expression lookup keeps recovery bounded.  The trigger below supplies future uniqueness
-- without making an old database containing duplicate bad history impossible to migrate.
CREATE INDEX idx_decision_log_proposal_review
  ON decision_log(project_id, json_extract(payload_json, '$.proposal_id'))
  WHERE kind = 'proposal_review'
    AND json_type(payload_json, '$.proposal_id') = 'text';

CREATE TRIGGER decision_log_kind_text_insert
BEFORE INSERT ON decision_log
WHEN typeof(NEW.kind) IS NOT 'text'
  OR nh_utf8_text(CAST(NEW.kind AS BLOB)) <> 1
BEGIN
  SELECT RAISE(ABORT, 'decision kind must be UTF-8 TEXT');
END;

CREATE TRIGGER decision_log_proposal_review_json_insert
BEFORE INSERT ON decision_log
WHEN NEW.kind = 'proposal_review'
BEGIN
  SELECT RAISE(ABORT, 'proposal review decision payload is ambiguous')
  WHERE json_type(NEW.payload_json) IS NOT 'object'
    OR nh_json_canonical(CAST(NEW.payload_json AS BLOB)) IS NULL
    OR EXISTS (
      SELECT 1
      FROM json_tree(NEW.payload_json)
      WHERE json_tree.key IS NOT NULL
      GROUP BY json_tree.parent, json_tree.key
      HAVING COUNT(*) > 1
    )
    OR EXISTS (
      SELECT 1
      FROM json_tree(NEW.payload_json)
      WHERE typeof(json_tree.key) = 'text'
        AND instr(json_tree.fullkey, char(92)) > 0
    );
END;

-- A future proposal-review row must be readable by ``decisions.read`` and must carry the same
-- text-first anchor contract as the durable proposal envelope.  Cast to BLOB before every Python
-- UDF so legacy SQLite TEXT containing invalid UTF-8 reaches our strict decoder safely.
CREATE TRIGGER decision_log_proposal_review_metadata_insert
BEFORE INSERT ON decision_log
WHEN NEW.kind = 'proposal_review'
BEGIN
  SELECT RAISE(ABORT, 'proposal review decision metadata is invalid')
  WHERE NOT (
    typeof(NEW.id) IS 'text'
    AND nh_utf8_text(CAST(NEW.id AS BLOB)) = 1
    AND typeof(NEW.ts) IS 'text'
    AND nh_utf8_text(CAST(NEW.ts AS BLOB)) = 1
    AND typeof(NEW.project_id) IS 'text'
    AND nh_utf8_text(CAST(NEW.project_id AS BLOB)) = 1
    AND typeof(NEW.actor) IS 'text'
    AND nh_utf8_text(CAST(NEW.actor AS BLOB)) = 1
    AND (
      NEW.subject_name IS NULL
      OR (
        typeof(NEW.subject_name) IS 'text'
        AND nh_utf8_text(CAST(NEW.subject_name AS BLOB)) = 1
      )
    )
    AND (
      (NEW.quote_text IS NULL AND NEW.quote_sha256 IS NULL)
      OR (
        typeof(NEW.quote_text) IS 'text'
        AND typeof(NEW.quote_sha256) IS 'text'
        AND NEW.quote_sha256 IS nh_sha256_text(CAST(NEW.quote_text AS BLOB))
      )
    )
    AND (
      NEW.chapter_number IS NULL
      OR (typeof(NEW.chapter_number) IS 'integer' AND NEW.chapter_number >= 1)
    )
    AND (
      NEW.para_index IS NULL
      OR (typeof(NEW.para_index) IS 'integer' AND NEW.para_index >= 0)
    )
  );
END;

-- Actual proposal/confirmation decisions are business-result outbox deliveries, never
-- free-standing writes.
-- Requiring an exact terminal row prevents one forged immutable log from occupying the proposal's
-- unique history slot before recovery can append the real author decision.
CREATE TRIGGER decision_log_proposal_review_matches_outbox_insert
BEFORE INSERT ON decision_log
WHEN NEW.kind = 'proposal_review'
  AND nh_json_canonical(CAST(NEW.payload_json AS BLOB)) IS NOT NULL
  AND json_type(NEW.payload_json) IS 'object'
  AND json_type(NEW.payload_json, '$.proposal_id') IS 'text'
  AND (
    SELECT COUNT(*) FROM json_each(NEW.payload_json)
    WHERE json_each.key = 'proposal_id'
  ) = 1
  AND NOT EXISTS (
    SELECT 1
    FROM json_tree(NEW.payload_json)
    WHERE json_tree.key IS NOT NULL
    GROUP BY json_tree.parent, json_tree.key
    HAVING COUNT(*) > 1
  )
  AND NOT EXISTS (
    SELECT 1
    FROM json_tree(NEW.payload_json)
    WHERE typeof(json_tree.key) = 'text'
      AND instr(json_tree.fullkey, char(92)) > 0
  )
  AND typeof(NEW.id) IS 'text'
  AND nh_utf8_text(CAST(NEW.id AS BLOB)) = 1
  AND typeof(NEW.ts) IS 'text'
  AND nh_utf8_text(CAST(NEW.ts AS BLOB)) = 1
  AND typeof(NEW.project_id) IS 'text'
  AND nh_utf8_text(CAST(NEW.project_id AS BLOB)) = 1
  AND typeof(NEW.actor) IS 'text'
  AND nh_utf8_text(CAST(NEW.actor AS BLOB)) = 1
  AND (
    NEW.subject_name IS NULL
    OR (
      typeof(NEW.subject_name) IS 'text'
      AND nh_utf8_text(CAST(NEW.subject_name AS BLOB)) = 1
    )
  )
  AND (
    (NEW.quote_text IS NULL AND NEW.quote_sha256 IS NULL)
    OR (
      typeof(NEW.quote_text) IS 'text'
      AND typeof(NEW.quote_sha256) IS 'text'
      AND NEW.quote_sha256 IS nh_sha256_text(CAST(NEW.quote_text AS BLOB))
    )
  )
  AND (
    NEW.chapter_number IS NULL
    OR (typeof(NEW.chapter_number) IS 'integer' AND NEW.chapter_number >= 1)
  )
  AND (
    NEW.para_index IS NULL
    OR (typeof(NEW.para_index) IS 'integer' AND NEW.para_index >= 0)
  )
BEGIN
  SELECT RAISE(ABORT, 'proposal review decision does not match durable audit envelope')
  WHERE NOT EXISTS (
    SELECT 1
    FROM proposal_set
    WHERE proposal_set.id = json_extract(NEW.payload_json, '$.proposal_id')
      AND proposal_set.project_id = NEW.project_id
      AND proposal_set.status <> 'PENDING'
      AND proposal_set.audit_envelope_json IS NOT NULL
      AND nh_json_canonical(CAST(NEW.payload_json AS BLOB))
          = nh_json_canonical(CAST(
              json_extract(proposal_set.audit_envelope_json, '$.payload') AS BLOB
            ))
      AND NEW.decision IS CASE proposal_set.resolution_action
        WHEN 'accept' THEN 'accept'
        WHEN 'edit' THEN 'edit'
        ELSE 'reject'
      END
      AND NEW.subject_name
          IS json_extract(proposal_set.audit_envelope_json, '$.subject_name')
      AND NEW.quote_text
          IS json_extract(proposal_set.audit_envelope_json, '$.quote_text')
      AND NEW.quote_sha256
          IS json_extract(proposal_set.audit_envelope_json, '$.quote_sha256')
      AND NEW.chapter_number
          IS json_extract(proposal_set.audit_envelope_json, '$.chapter_number')
      AND NEW.para_index
          IS json_extract(proposal_set.audit_envelope_json, '$.para_index')
  );
END;

CREATE TRIGGER decision_log_one_proposal_review_insert
BEFORE INSERT ON decision_log
WHEN NEW.kind = 'proposal_review'
BEGIN
  SELECT RAISE(ABORT, 'proposal review decision payload is ambiguous')
  WHERE json_type(NEW.payload_json) IS NOT 'object'
    OR json_type(NEW.payload_json, '$.proposal_id') IS NOT 'text'
    OR (
      SELECT COUNT(*) FROM json_each(NEW.payload_json)
      WHERE json_each.key = 'proposal_id'
    ) <> 1;

  SELECT RAISE(ABORT, 'proposal review decision already exists')
  WHERE EXISTS (
    SELECT 1 FROM decision_log
    WHERE project_id = NEW.project_id
      AND (
        kind = 'proposal_review'
        OR (
          typeof(kind) = 'blob'
          AND nh_utf8_text(CAST(kind AS BLOB)) = 1
          AND CAST(kind AS TEXT) = 'proposal_review'
        )
      )
      AND EXISTS (
        SELECT 1 FROM json_each(decision_log.payload_json)
        WHERE json_each.key = 'proposal_id'
          AND json_each.type = 'text'
          AND json_each.value = json_extract(NEW.payload_json, '$.proposal_id')
      )
  );
END;

-- Passive confirmation receipts are invisible terminal proposal rows.  They reuse the same
-- immutable outbox and attachment machinery without entering the PENDING review queue.
CREATE TRIGGER proposal_confirmation_metadata_insert
BEFORE INSERT ON proposal_set
WHEN NOT (
  (
    NEW.kind IS 'provisional_confirm'
    AND NEW.confirmation_fact_kind IS NOT NULL
    AND NEW.confirmation_request_hash IS NOT NULL
    AND NEW.status IS 'ACCEPTED'
    AND NEW.resolution_action IS 'accept'
    AND NEW.resolved_canon_version IS NEW.base_canon_version + 1
    AND json_type(NEW.items_json) IS 'array'
    AND nh_json_canonical(CAST(NEW.items_json AS BLOB)) IS NOT NULL
    AND json_array_length(NEW.items_json) > 0
    AND json_array_length(NEW.items_json) IS NEW.item_count
  )
  OR (
    NEW.kind IS NOT 'provisional_confirm'
    AND NEW.confirmation_fact_kind IS NULL
    AND NEW.confirmation_request_hash IS NULL
  )
)
BEGIN
  SELECT RAISE(ABORT, 'proposal confirmation receipt metadata is invalid');
END;

CREATE TRIGGER proposal_confirmation_metadata_update
BEFORE UPDATE OF kind, confirmation_fact_kind, confirmation_request_hash, status,
                 resolution_action, base_canon_version, resolved_canon_version,
                 items_json, item_count
ON proposal_set
WHEN NOT (
  (
    NEW.kind IS 'provisional_confirm'
    AND NEW.confirmation_fact_kind IS NOT NULL
    AND NEW.confirmation_request_hash IS NOT NULL
    AND NEW.status IS 'ACCEPTED'
    AND NEW.resolution_action IS 'accept'
    AND NEW.resolved_canon_version IS NEW.base_canon_version + 1
    AND json_type(NEW.items_json) IS 'array'
    AND nh_json_canonical(CAST(NEW.items_json AS BLOB)) IS NOT NULL
    AND json_array_length(NEW.items_json) > 0
    AND json_array_length(NEW.items_json) IS NEW.item_count
  )
  OR (
    NEW.kind IS NOT 'provisional_confirm'
    AND NEW.confirmation_fact_kind IS NULL
    AND NEW.confirmation_request_hash IS NULL
  )
)
BEGIN
  SELECT RAISE(ABORT, 'proposal confirmation receipt metadata is invalid');
END;

CREATE TRIGGER proposal_confirmation_receipt_immutable
BEFORE UPDATE OF confirmation_fact_kind, confirmation_request_hash, items_json, item_count
ON proposal_set
WHEN OLD.kind IS 'provisional_confirm'
  AND (
    NEW.confirmation_fact_kind IS NOT OLD.confirmation_fact_kind
    OR NEW.confirmation_request_hash IS NOT OLD.confirmation_request_hash
    OR NEW.items_json IS NOT OLD.items_json
    OR NEW.item_count IS NOT OLD.item_count
  )
BEGIN
  SELECT RAISE(ABORT, 'proposal confirmation receipt is immutable');
END;

CREATE TRIGGER proposal_confirmation_event_link_insert
BEFORE INSERT ON proposal_event
WHEN (
  SELECT kind FROM proposal_set WHERE id = NEW.proposal_id
) IS 'provisional_confirm'
BEGIN
  SELECT RAISE(ABORT, 'confirmation receipt fact kind does not match event link')
  WHERE (
    SELECT confirmation_fact_kind FROM proposal_set WHERE id = NEW.proposal_id
  ) IS NOT 'event';
  SELECT RAISE(ABORT, 'provisional event is already confirmed')
  WHERE EXISTS (
    SELECT 1
    FROM proposal_event AS existing
    JOIN proposal_set AS owner ON owner.id = existing.proposal_id
    WHERE owner.kind = 'provisional_confirm'
      AND existing.event_id = NEW.event_id
      AND existing.proposal_id <> NEW.proposal_id
  );
END;

CREATE TRIGGER proposal_confirmation_edge_link_insert
BEFORE INSERT ON proposal_edge
WHEN (
  SELECT kind FROM proposal_set WHERE id = NEW.proposal_id
) IS 'provisional_confirm'
BEGIN
  SELECT RAISE(ABORT, 'confirmation receipt fact kind does not match edge link')
  WHERE (
    SELECT confirmation_fact_kind FROM proposal_set WHERE id = NEW.proposal_id
  ) IS NOT 'edge';
  SELECT RAISE(ABORT, 'provisional edge is already confirmed')
  WHERE EXISTS (
    SELECT 1
    FROM proposal_edge AS existing
    JOIN proposal_set AS owner ON owner.id = existing.proposal_id
    WHERE owner.kind = 'provisional_confirm'
      AND existing.edge_id = NEW.edge_id
      AND existing.proposal_id <> NEW.proposal_id
  );
END;

CREATE TRIGGER proposal_confirmation_event_link_immutable
BEFORE UPDATE ON proposal_event
WHEN (
  SELECT kind FROM proposal_set WHERE id = OLD.proposal_id
) IS 'provisional_confirm'
  OR (
    SELECT kind FROM proposal_set WHERE id = NEW.proposal_id
  ) IS 'provisional_confirm'
BEGIN
  SELECT RAISE(ABORT, 'proposal confirmation source links are immutable');
END;

CREATE TRIGGER proposal_confirmation_event_link_delete
BEFORE DELETE ON proposal_event
WHEN (
  SELECT kind FROM proposal_set WHERE id = OLD.proposal_id
) IS 'provisional_confirm'
BEGIN
  SELECT RAISE(ABORT, 'proposal confirmation source links are immutable');
END;

CREATE TRIGGER proposal_confirmation_edge_link_immutable
BEFORE UPDATE ON proposal_edge
WHEN (
  SELECT kind FROM proposal_set WHERE id = OLD.proposal_id
) IS 'provisional_confirm'
  OR (
    SELECT kind FROM proposal_set WHERE id = NEW.proposal_id
  ) IS 'provisional_confirm'
BEGIN
  SELECT RAISE(ABORT, 'proposal confirmation source links are immutable');
END;

CREATE TRIGGER proposal_confirmation_edge_link_delete
BEFORE DELETE ON proposal_edge
WHEN (
  SELECT kind FROM proposal_set WHERE id = OLD.proposal_id
) IS 'provisional_confirm'
BEGIN
  SELECT RAISE(ABORT, 'proposal confirmation source links are immutable');
END;

-- New writes must make the proposal row a complete, self-consistent outbox record.
CREATE TRIGGER proposal_resolution_metadata_insert
BEFORE INSERT ON proposal_set
WHEN NEW.id IS NULL OR NOT (
  (
    NEW.status IS 'PENDING'
    AND NEW.resolution_action IS NULL
    AND NEW.resolved_canon_version IS NULL
    AND NEW.audit_envelope_json IS NULL
    AND NEW.decision_log_id IS NULL
  )
  OR (
    NEW.status IS NOT 'PENDING'
    AND NEW.resolution_action IS NOT NULL
    AND NEW.resolved_canon_version IS NOT NULL
    AND NEW.audit_envelope_json IS NOT NULL
    AND nh_json_canonical(CAST(NEW.audit_envelope_json AS BLOB)) IS NOT NULL
    AND json_type(NEW.audit_envelope_json, '$.payload') IS 'object'
    AND json_type(NEW.audit_envelope_json, '$.payload.proposal_id') IS 'text'
    AND json_type(NEW.audit_envelope_json, '$.payload.action') IS 'text'
    AND json_type(NEW.audit_envelope_json, '$.payload.status') IS 'text'
    AND json_type(NEW.audit_envelope_json, '$.payload.kind') IS 'text'
    AND json_type(NEW.audit_envelope_json, '$.payload.canon_version') IS 'integer'
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json)
      WHERE json_each.key = 'payload'
    ) = 1
    AND NOT EXISTS (
      SELECT 1 FROM json_each(NEW.audit_envelope_json)
      WHERE json_each.key NOT IN (
        'payload', 'subject_name', 'quote_text', 'quote_sha256',
        'chapter_number', 'para_index'
      )
    )
    AND (
      json_type(NEW.audit_envelope_json, '$.subject_name') IS NULL
      OR json_type(NEW.audit_envelope_json, '$.subject_name') IS 'null'
      OR json_type(NEW.audit_envelope_json, '$.subject_name') IS 'text'
    )
    AND (
      (
        (
          json_type(NEW.audit_envelope_json, '$.quote_text') IS NULL
          OR json_type(NEW.audit_envelope_json, '$.quote_text') IS 'null'
        )
        AND (
          json_type(NEW.audit_envelope_json, '$.quote_sha256') IS NULL
          OR json_type(NEW.audit_envelope_json, '$.quote_sha256') IS 'null'
        )
      )
      OR (
        json_type(NEW.audit_envelope_json, '$.quote_text') IS 'text'
        AND json_type(NEW.audit_envelope_json, '$.quote_sha256') IS 'text'
        AND json_extract(NEW.audit_envelope_json, '$.quote_sha256')
            IS nh_sha256_text(CAST(
              json_extract(NEW.audit_envelope_json, '$.quote_text') AS BLOB
            ))
      )
    )
    AND (
      json_type(NEW.audit_envelope_json, '$.chapter_number') IS NULL
      OR json_type(NEW.audit_envelope_json, '$.chapter_number') IS 'null'
      OR (
        json_type(NEW.audit_envelope_json, '$.chapter_number') IS 'integer'
        AND json_extract(NEW.audit_envelope_json, '$.chapter_number') >= 1
      )
    )
    AND (
      json_type(NEW.audit_envelope_json, '$.para_index') IS NULL
      OR json_type(NEW.audit_envelope_json, '$.para_index') IS 'null'
      OR (
        json_type(NEW.audit_envelope_json, '$.para_index') IS 'integer'
        AND json_extract(NEW.audit_envelope_json, '$.para_index') >= 0
      )
    )
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json, '$.payload')
      WHERE json_each.key = 'proposal_id'
    ) = 1
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json, '$.payload')
      WHERE json_each.key = 'action'
    ) = 1
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json, '$.payload')
      WHERE json_each.key = 'status'
    ) = 1
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json, '$.payload')
      WHERE json_each.key = 'kind'
    ) = 1
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json, '$.payload')
      WHERE json_each.key = 'canon_version'
    ) = 1
    AND NOT EXISTS (
      SELECT 1
      FROM json_tree(NEW.audit_envelope_json)
      WHERE json_tree.key IS NOT NULL
      GROUP BY json_tree.parent, json_tree.key
      HAVING COUNT(*) > 1
    )
    AND NOT EXISTS (
      SELECT 1
      FROM json_tree(NEW.audit_envelope_json)
      WHERE typeof(json_tree.key) = 'text'
        AND instr(json_tree.fullkey, char(92)) > 0
    )
    AND json_extract(NEW.audit_envelope_json, '$.payload.proposal_id') IS NEW.id
    AND json_extract(NEW.audit_envelope_json, '$.payload.action') IS NEW.resolution_action
    AND json_extract(NEW.audit_envelope_json, '$.payload.status') IS NEW.status
    AND json_extract(NEW.audit_envelope_json, '$.payload.kind') IS NEW.kind
    AND json_extract(NEW.audit_envelope_json, '$.payload.canon_version')
        IS NEW.resolved_canon_version
    AND (
      (NEW.status IS 'ACCEPTED' AND NEW.resolution_action IS 'accept'
        AND NEW.resolved_canon_version IS NEW.base_canon_version + 1)
      OR (NEW.status IS 'EDITED' AND NEW.resolution_action IS 'edit'
        AND NEW.resolved_canon_version IS NEW.base_canon_version + 1)
      OR (NEW.status IS 'REJECTED'
        AND (NEW.resolution_action IS 'reject' OR NEW.resolution_action IS 'bystander')
        AND NEW.resolved_canon_version IS NEW.base_canon_version)
    )
    AND (NEW.resolution_action IS NOT 'bystander' OR NEW.kind IS 'new_character')
  )
)
BEGIN
  SELECT RAISE(ABORT, 'proposal resolution metadata is incomplete or incoherent');
END;

CREATE TRIGGER proposal_resolution_metadata_update
BEFORE UPDATE OF id, project_id, status, resolution_action, resolved_canon_version,
                 audit_envelope_json, decision_log_id, base_canon_version, kind
ON proposal_set
WHEN NEW.id IS NULL OR NOT (
  (
    NEW.status IS 'PENDING'
    AND NEW.resolution_action IS NULL
    AND NEW.resolved_canon_version IS NULL
    AND NEW.audit_envelope_json IS NULL
    AND NEW.decision_log_id IS NULL
  )
  OR (
    NEW.status IS NOT 'PENDING'
    AND NEW.resolution_action IS NOT NULL
    AND NEW.resolved_canon_version IS NOT NULL
    AND NEW.audit_envelope_json IS NOT NULL
    AND nh_json_canonical(CAST(NEW.audit_envelope_json AS BLOB)) IS NOT NULL
    AND json_type(NEW.audit_envelope_json, '$.payload') IS 'object'
    AND json_type(NEW.audit_envelope_json, '$.payload.proposal_id') IS 'text'
    AND json_type(NEW.audit_envelope_json, '$.payload.action') IS 'text'
    AND json_type(NEW.audit_envelope_json, '$.payload.status') IS 'text'
    AND json_type(NEW.audit_envelope_json, '$.payload.kind') IS 'text'
    AND json_type(NEW.audit_envelope_json, '$.payload.canon_version') IS 'integer'
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json)
      WHERE json_each.key = 'payload'
    ) = 1
    AND NOT EXISTS (
      SELECT 1 FROM json_each(NEW.audit_envelope_json)
      WHERE json_each.key NOT IN (
        'payload', 'subject_name', 'quote_text', 'quote_sha256',
        'chapter_number', 'para_index'
      )
    )
    AND (
      json_type(NEW.audit_envelope_json, '$.subject_name') IS NULL
      OR json_type(NEW.audit_envelope_json, '$.subject_name') IS 'null'
      OR json_type(NEW.audit_envelope_json, '$.subject_name') IS 'text'
    )
    AND (
      (
        (
          json_type(NEW.audit_envelope_json, '$.quote_text') IS NULL
          OR json_type(NEW.audit_envelope_json, '$.quote_text') IS 'null'
        )
        AND (
          json_type(NEW.audit_envelope_json, '$.quote_sha256') IS NULL
          OR json_type(NEW.audit_envelope_json, '$.quote_sha256') IS 'null'
        )
      )
      OR (
        json_type(NEW.audit_envelope_json, '$.quote_text') IS 'text'
        AND json_type(NEW.audit_envelope_json, '$.quote_sha256') IS 'text'
        AND json_extract(NEW.audit_envelope_json, '$.quote_sha256')
            IS nh_sha256_text(CAST(
              json_extract(NEW.audit_envelope_json, '$.quote_text') AS BLOB
            ))
      )
    )
    AND (
      json_type(NEW.audit_envelope_json, '$.chapter_number') IS NULL
      OR json_type(NEW.audit_envelope_json, '$.chapter_number') IS 'null'
      OR (
        json_type(NEW.audit_envelope_json, '$.chapter_number') IS 'integer'
        AND json_extract(NEW.audit_envelope_json, '$.chapter_number') >= 1
      )
    )
    AND (
      json_type(NEW.audit_envelope_json, '$.para_index') IS NULL
      OR json_type(NEW.audit_envelope_json, '$.para_index') IS 'null'
      OR (
        json_type(NEW.audit_envelope_json, '$.para_index') IS 'integer'
        AND json_extract(NEW.audit_envelope_json, '$.para_index') >= 0
      )
    )
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json, '$.payload')
      WHERE json_each.key = 'proposal_id'
    ) = 1
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json, '$.payload')
      WHERE json_each.key = 'action'
    ) = 1
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json, '$.payload')
      WHERE json_each.key = 'status'
    ) = 1
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json, '$.payload')
      WHERE json_each.key = 'kind'
    ) = 1
    AND (
      SELECT COUNT(*) FROM json_each(NEW.audit_envelope_json, '$.payload')
      WHERE json_each.key = 'canon_version'
    ) = 1
    AND NOT EXISTS (
      SELECT 1
      FROM json_tree(NEW.audit_envelope_json)
      WHERE json_tree.key IS NOT NULL
      GROUP BY json_tree.parent, json_tree.key
      HAVING COUNT(*) > 1
    )
    AND NOT EXISTS (
      SELECT 1
      FROM json_tree(NEW.audit_envelope_json)
      WHERE typeof(json_tree.key) = 'text'
        AND instr(json_tree.fullkey, char(92)) > 0
    )
    AND json_extract(NEW.audit_envelope_json, '$.payload.proposal_id') IS NEW.id
    AND json_extract(NEW.audit_envelope_json, '$.payload.action') IS NEW.resolution_action
    AND json_extract(NEW.audit_envelope_json, '$.payload.status') IS NEW.status
    AND json_extract(NEW.audit_envelope_json, '$.payload.kind') IS NEW.kind
    AND json_extract(NEW.audit_envelope_json, '$.payload.canon_version')
        IS NEW.resolved_canon_version
    AND (
      (NEW.status IS 'ACCEPTED' AND NEW.resolution_action IS 'accept'
        AND NEW.resolved_canon_version IS NEW.base_canon_version + 1)
      OR (NEW.status IS 'EDITED' AND NEW.resolution_action IS 'edit'
        AND NEW.resolved_canon_version IS NEW.base_canon_version + 1)
      OR (NEW.status IS 'REJECTED'
        AND (NEW.resolution_action IS 'reject' OR NEW.resolution_action IS 'bystander')
        AND NEW.resolved_canon_version IS NEW.base_canon_version)
    )
    AND (NEW.resolution_action IS NOT 'bystander' OR NEW.kind IS 'new_character')
  )
)
BEGIN
  SELECT RAISE(ABORT, 'proposal resolution metadata is incomplete or incoherent');
END;

CREATE TRIGGER proposal_resolution_metadata_immutable
BEFORE UPDATE OF id, project_id, status, resolution_action, resolved_canon_version,
                 audit_envelope_json, base_canon_version, kind
ON proposal_set
WHEN OLD.status <> 'PENDING'
  AND (
    NEW.id IS NOT OLD.id
    OR NEW.project_id IS NOT OLD.project_id
    OR NEW.status IS NOT OLD.status
    OR NEW.resolution_action IS NOT OLD.resolution_action
    OR NEW.resolved_canon_version IS NOT OLD.resolved_canon_version
    OR NEW.audit_envelope_json IS NOT OLD.audit_envelope_json
    OR NEW.base_canon_version IS NOT OLD.base_canon_version
    OR NEW.kind IS NOT OLD.kind
  )
BEGIN
  SELECT RAISE(ABORT, 'terminal proposal resolution metadata is immutable');
END;

-- SQLite json() preserves object key order while Python serializes audit payloads with sorted keys.
-- The connection's strict canonicalizer validates the shared representation and compares JSON
-- structure without weakening array order or numeric/string types.
CREATE TRIGGER proposal_decision_attachment_matches_insert
BEFORE INSERT ON proposal_set
WHEN NEW.decision_log_id IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'proposal decision does not match durable audit envelope')
  WHERE NOT EXISTS (
    SELECT 1 FROM decision_log
    WHERE decision_log.id = NEW.decision_log_id
      AND decision_log.project_id = NEW.project_id
      AND decision_log.kind = 'proposal_review'
      AND nh_json_canonical(CAST(decision_log.payload_json AS BLOB))
          = nh_json_canonical(CAST(
              json_extract(NEW.audit_envelope_json, '$.payload') AS BLOB
            ))
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
      AND nh_json_canonical(CAST(decision_log.payload_json AS BLOB))
          = nh_json_canonical(CAST(
              json_extract(NEW.audit_envelope_json, '$.payload') AS BLOB
            ))
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
