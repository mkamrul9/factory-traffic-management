CREATE TABLE junctions (
    junction_id TEXT PRIMARY KEY,
    configuration_json TEXT NOT NULL CHECK (configuration_json::jsonb IS NOT NULL),
    created_at TEXT NOT NULL
);

CREATE TABLE waiting_vehicles (
    junction_id TEXT NOT NULL REFERENCES junctions(junction_id),
    vehicle_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('NORTH', 'SOUTH', 'EAST', 'WEST')),
    vehicle_type TEXT NOT NULL CHECK (
        vehicle_type IN ('FORKLIFT', 'TRUCK', 'EMPLOYEE_VEHICLE', 'EMERGENCY')
    ),
    arrived_at TEXT NOT NULL,
    sensor_timestamp TEXT NOT NULL,
    arrival_event_id TEXT NOT NULL,
    sequence_no BIGINT NOT NULL CHECK (sequence_no >= 0),
    PRIMARY KEY (junction_id, vehicle_id)
);
CREATE INDEX waiting_by_direction ON waiting_vehicles(junction_id, direction, arrived_at);

CREATE TABLE event_receipts (
    event_id TEXT PRIMARY KEY,
    junction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (payload_json::jsonb IS NOT NULL),
    outcome TEXT NOT NULL CHECK (outcome IN ('ACCEPTED', 'IGNORED', 'REJECTED')),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    received_at TEXT NOT NULL,
    status_code INTEGER NOT NULL
);

CREATE TABLE sequence_cursors (
    junction_id TEXT NOT NULL REFERENCES junctions(junction_id),
    direction TEXT NOT NULL CHECK (direction IN ('NORTH', 'SOUTH', 'EAST', 'WEST')),
    sequence_no BIGINT NOT NULL CHECK (sequence_no >= 0),
    PRIMARY KEY (junction_id, direction)
);

CREATE TABLE audit_history (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    junction_id TEXT,
    event_type TEXT NOT NULL,
    event_id TEXT,
    timestamp TEXT NOT NULL,
    details_json TEXT NOT NULL CHECK (details_json::jsonb IS NOT NULL)
);
CREATE INDEX history_by_junction ON audit_history(junction_id, id DESC);

CREATE TABLE junction_runtime (
    junction_id TEXT PRIMARY KEY REFERENCES junctions(junction_id),
    state_json TEXT NOT NULL CHECK (state_json::jsonb IS NOT NULL)
);

CREATE TABLE controller_commands (
    command_id TEXT PRIMARY KEY,
    junction_id TEXT NOT NULL REFERENCES junctions(junction_id),
    batch_id TEXT NOT NULL,
    epoch TEXT NOT NULL,
    signal_version BIGINT NOT NULL CHECK (signal_version > 0),
    direction TEXT NOT NULL CHECK (direction IN ('NORTH', 'SOUTH', 'EAST', 'WEST')),
    requested_state TEXT NOT NULL CHECK (requested_state IN ('RED', 'YELLOW', 'GREEN')),
    requested_at TEXT NOT NULL,
    deadline TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('PENDING', 'ACKNOWLEDGED', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'INTERRUPTED')
    ),
    actual_state TEXT CHECK (actual_state IN ('RED', 'YELLOW', 'GREEN')),
    acknowledged_at TEXT,
    UNIQUE (batch_id, direction)
);
CREATE INDEX pending_controller_commands ON controller_commands(junction_id, status, batch_id);
