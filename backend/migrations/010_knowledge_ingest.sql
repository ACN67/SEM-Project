CREATE TABLE IF NOT EXISTS documents (
    document_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS document_filenames (
    filename_key VARBINARY(1020) PRIMARY KEY,
    document_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(document_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS document_versions (
    registration_order BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    document_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
    document_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    sha256 CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE,
    original_filename VARCHAR(255) NOT NULL,
    storage_key VARCHAR(100) CHARACTER SET ascii NOT NULL,
    metadata_json JSON NOT NULL,
    ingest_status ENUM('QUEUED','PROCESSING','READY','FAILED') NOT NULL DEFAULT 'QUEUED',
    error_json JSON NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX document_history (document_id, registration_order),
    FOREIGN KEY (document_id) REFERENCES documents(document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
