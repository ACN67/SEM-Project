CREATE TABLE IF NOT EXISTS knowledge_labels (
    document_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    payload JSON NOT NULL,
    FOREIGN KEY (document_version_id) REFERENCES document_versions(document_version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
