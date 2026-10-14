-- km3 初始架构。见 docs/架构设计/服务器端程序架构.md §二。
-- 用编号 SQL + psql 顺序执行，不引入迁移框架。

BEGIN;

CREATE TABLE IF NOT EXISTS environments (
    id   SMALLSERIAL PRIMARY KEY,
    code VARCHAR(20) UNIQUE NOT NULL,
    name VARCHAR(50) NOT NULL
);
INSERT INTO environments (code, name) VALUES ('demo', '演示'), ('prod', '生产')
    ON CONFLICT (code) DO NOTHING;

CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    username      VARCHAR(50) UNIQUE NOT NULL,
    email         VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(20) NOT NULL DEFAULT 'operator',
    status        VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id VARCHAR(64) PRIMARY KEY,
    user_id    BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    client_ip  INET
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS user_invitations (
    id          BIGSERIAL PRIMARY KEY,
    email       VARCHAR(255) NOT NULL,
    role        VARCHAR(20) NOT NULL,
    token       VARCHAR(64) UNIQUE NOT NULL,
    invited_by  BIGINT NOT NULL REFERENCES users(id),
    expires_at  TIMESTAMPTZ NOT NULL,
    accepted_at TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS devices (
    id               BIGSERIAL PRIMARY KEY,
    device_id        VARCHAR(50) UNIQUE NOT NULL,
    name             VARCHAR(100) NOT NULL,
    model            VARCHAR(50) NOT NULL,
    env_id           SMALLINT NOT NULL REFERENCES environments(id),
    firmware_version VARCHAR(20),
    status           VARCHAR(20) NOT NULL DEFAULT 'offline',
    last_seen_at     TIMESTAMPTZ,
    location         VARCHAR(255),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_devices_env ON devices(env_id);
CREATE INDEX IF NOT EXISTS idx_devices_status ON devices(status);

CREATE TABLE IF NOT EXISTS device_public_keys (
    device_id   VARCHAR(50) NOT NULL,
    public_key  BYTEA NOT NULL,
    fingerprint VARCHAR(64) NOT NULL,
    valid_from  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at  TIMESTAMPTZ,
    PRIMARY KEY (device_id, fingerprint)
);

CREATE TABLE IF NOT EXISTS device_commands (
    id           BIGSERIAL PRIMARY KEY,
    command_id   UUID NOT NULL UNIQUE,
    device_id    VARCHAR(50) NOT NULL,
    user_id      BIGINT REFERENCES users(id),
    command_type VARCHAR(50) NOT NULL,
    command_data JSONB NOT NULL,
    priority     SMALLINT NOT NULL DEFAULT 5,
    status       VARCHAR(20) NOT NULL DEFAULT 'pending',
    timeout_at   TIMESTAMPTZ NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    sent_at      TIMESTAMPTZ,
    acked_at     TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    result       JSONB,
    error_code   VARCHAR(40),
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_cmd_device_status ON device_commands(device_id, status);
CREATE INDEX IF NOT EXISTS idx_cmd_timeout ON device_commands(timeout_at)
    WHERE status IN ('pending','queued','sent','acked');

CREATE TABLE IF NOT EXISTS command_dedup (
    device_id  VARCHAR(50) NOT NULL,
    command_id UUID NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (device_id, command_id)
);

CREATE TABLE IF NOT EXISTS firmware (
    id          BIGSERIAL PRIMARY KEY,
    version     VARCHAR(20) NOT NULL,
    model       VARCHAR(50) NOT NULL,
    env_id      SMALLINT NOT NULL REFERENCES environments(id),
    file_path   VARCHAR(500) NOT NULL,
    file_size   BIGINT NOT NULL,
    file_hash   VARCHAR(64) NOT NULL,
    signature   BYTEA NOT NULL,
    changelog   TEXT,
    is_stable   BOOLEAN NOT NULL DEFAULT false,
    min_version VARCHAR(20),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by  BIGINT REFERENCES users(id),
    UNIQUE(version, model, env_id)
);

CREATE TABLE IF NOT EXISTS ota_deployments (
    id          BIGSERIAL PRIMARY KEY,
    firmware_id BIGINT NOT NULL REFERENCES firmware(id),
    device_id   VARCHAR(50) NOT NULL,
    status      VARCHAR(20) NOT NULL DEFAULT 'pending',
    progress    SMALLINT NOT NULL DEFAULT 0,
    prev_version VARCHAR(20),
    kind        VARCHAR(20) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at  TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    error_code  VARCHAR(40),
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS runtime_configs (
    id           BIGSERIAL PRIMARY KEY,
    device_id    VARCHAR(50) NOT NULL,
    config_key   VARCHAR(100) NOT NULL,
    config_value JSONB NOT NULL,
    version      INTEGER NOT NULL DEFAULT 1,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by   BIGINT REFERENCES users(id),
    UNIQUE(device_id, config_key)
);

CREATE TABLE IF NOT EXISTS device_logs (
    id        BIGSERIAL PRIMARY KEY,
    device_id VARCHAR(50) NOT NULL,
    ts        TIMESTAMPTZ NOT NULL,
    level     SMALLINT NOT NULL,
    module    VARCHAR(40) NOT NULL,
    message   TEXT NOT NULL,
    fields    JSONB,
    trace_id  UUID
);
CREATE INDEX IF NOT EXISTS idx_logs_device_ts ON device_logs(device_id, ts DESC);

CREATE TABLE IF NOT EXISTS audit_logs (
    id     BIGSERIAL PRIMARY KEY,
    user_id BIGINT REFERENCES users(id),
    action VARCHAR(50) NOT NULL,
    target VARCHAR(120),
    detail JSONB,
    ts     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS work_hours (
    id         BIGSERIAL PRIMARY KEY,
    device_id  VARCHAR(50) NOT NULL,
    user_id    BIGINT REFERENCES users(id),
    date       DATE NOT NULL,
    category   VARCHAR(50) NOT NULL,
    hours      NUMERIC(10,2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(device_id, user_id, date, category)
);

-- 时序数据：按月分区，不用 InfluxDB。schema_version 用于跨版本回滚兼容。
CREATE TABLE IF NOT EXISTS sensor_data (
    device_id      VARCHAR(50) NOT NULL,
    ts             TIMESTAMPTZ NOT NULL,
    sensor_type    VARCHAR(40) NOT NULL,
    sensor_id      VARCHAR(40),
    value_num      DOUBLE PRECISION,
    value_json     JSONB,
    quality        SMALLINT,
    schema_version SMALLINT NOT NULL DEFAULT 1
) PARTITION BY RANGE (ts);

CREATE TABLE IF NOT EXISTS sensor_data_2026_10 PARTITION OF sensor_data
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE IF NOT EXISTS sensor_data_2026_11 PARTITION OF sensor_data
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE INDEX IF NOT EXISTS idx_sensor_device_ts ON sensor_data (device_id, sensor_type, ts DESC);

COMMIT;
