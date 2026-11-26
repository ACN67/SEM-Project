CREATE TABLE IF NOT EXISTS chunks (
    chunk_id CHAR(64) CHARACTER SET ascii PRIMARY KEY,
    document_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    excerpt TEXT NOT NULL,
    industry VARCHAR(255),
    INDEX (document_version_id),
    FOREIGN KEY (document_version_id) REFERENCES document_versions(document_version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS evidences (
    evidence_id CHAR(64) CHARACTER SET ascii PRIMARY KEY,
    chunk_id CHAR(64) CHARACTER SET ascii NOT NULL,
    document_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload JSON NOT NULL,
    INDEX (document_version_id),
    FOREIGN KEY (document_version_id) REFERENCES document_versions(document_version_id),
    FOREIGN KEY (chunk_id) REFERENCES chunks(chunk_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS metrics (
    metric_id CHAR(64) CHARACTER SET ascii PRIMARY KEY,
    evidence_id CHAR(64) CHARACTER SET ascii NOT NULL,
    document_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payload JSON NOT NULL,
    INDEX (document_version_id),
    FOREIGN KEY (document_version_id) REFERENCES document_versions(document_version_id),
    FOREIGN KEY (evidence_id) REFERENCES evidences(evidence_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
