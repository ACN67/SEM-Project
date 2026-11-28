-- Report orchestration tables. Apply after 010-012 on the same database.
-- IDs are opaque 32-character ASCII strings and JSON columns keep contract payloads verbatim.

CREATE TABLE IF NOT EXISTS reports (
    report_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    report_type VARCHAR(64) NOT NULL,
    template_id VARCHAR(128) NOT NULL,
    template_version INT NOT NULL,
    latest_job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    job_status ENUM('QUEUED','RUNNING','COMPLETED','COMPLETED_WITH_WARNINGS','FAILED','CANCELLED') NOT NULL DEFAULT 'QUEUED',
    current_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX report_order (created_at DESC, report_id DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS jobs (
    job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    report_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    run_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    previous_job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    status ENUM('QUEUED','RUNNING','COMPLETED','COMPLETED_WITH_WARNINGS','FAILED','CANCELLED') NOT NULL DEFAULT 'QUEUED',
    stage ENUM('PLANNING','GENERATING','VALIDATING') NULL,
    spec_json JSON NULL,
    frozen_json JSON NULL,
    context_json JSON NULL,
    result_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    estimated_cost_cny DECIMAL(10, 4) NOT NULL DEFAULT 0,
    heartbeat_at TIMESTAMP(6) NULL,
    error_json JSON NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX job_claim (status, created_at),
    FOREIGN KEY (report_id) REFERENCES reports(report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS chapter_runs (
    job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    chapter_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    status ENUM('PENDING','WAITING_DEPENDENCY','RUNNING','SUCCEEDED','FAILED','SKIPPED') NOT NULL DEFAULT 'PENDING',
    attempt_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    retries INT NOT NULL DEFAULT 0,
    content_json JSON NULL,
    error_json JSON NULL,
    PRIMARY KEY (job_id, chapter_id),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS report_versions (
    report_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    parent_version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    document_json JSON NOT NULL,
    exportable BOOLEAN NOT NULL DEFAULT FALSE,
    stale_chapter_ids JSON NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (report_id, version_id),
    INDEX version_order (report_id, created_at DESC),
    FOREIGN KEY (report_id) REFERENCES reports(report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS exports (
    export_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    report_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    version_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    format ENUM('docx','pdf') NOT NULL,
    status ENUM('QUEUED','RUNNING','READY','FAILED') NOT NULL DEFAULT 'QUEUED',
    storage_key VARCHAR(255) CHARACTER SET ascii NULL,
    checksum CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    size_bytes BIGINT UNSIGNED NULL,
    error_json JSON NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
    INDEX export_claim (status, created_at),
    FOREIGN KEY (report_id, version_id) REFERENCES report_versions(report_id, version_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS idempotency_keys (
    operation VARCHAR(32) NOT NULL,
    target_id VARCHAR(191) NOT NULL,
    idempotency_key VARCHAR(128) NOT NULL,
    request_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    report_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NULL,
    response_json JSON NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (operation, target_id, idempotency_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS model_usage (
    usage_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    job_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    chapter_id VARCHAR(128) CHARACTER SET ascii COLLATE ascii_bin NULL,
    model_id VARCHAR(128) NOT NULL,
    prompt_tokens BIGINT UNSIGNED NOT NULL DEFAULT 0,
    completion_tokens BIGINT UNSIGNED NOT NULL DEFAULT 0,
    cost_cny DECIMAL(10, 4) NOT NULL DEFAULT 0,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    INDEX usage_by_job (job_id),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS admin_sessions (
    session_id CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    expires_at TIMESTAMP(6) NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
