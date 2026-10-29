CREATE TABLE junctions (
    junction_id TEXT PRIMARY KEY,
    configuration_json TEXT NOT NULL CHECK (json_valid(configuration_json)),
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
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    PRIMARY KEY (junction_id, vehicle_id)
);
CREATE INDEX waiting_by_direction ON waiting_vehicles(junction_id, direction, arrived_at);

-- Rejected events can refer to unknown junctions, so receipts have no junction foreign key.
CREATE TABLE sensor_event_receipts (
    event_id TEXT PRIMARY KEY,
    junction_id TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    outcome TEXT NOT NULL CHECK (outcome IN ('ACCEPTED', 'IGNORED', 'REJECTED')),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    received_at TEXT NOT NULL,
    status_code INTEGER NOT NULL
);

CREATE TABLE sequence_cursors (
    junction_id TEXT NOT NULL REFERENCES junctions(junction_id),
    direction TEXT NOT NULL CHECK (direction IN ('NORTH', 'SOUTH', 'EAST', 'WEST')),
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    PRIMARY KEY (junction_id, direction)
);

-- Invalid or unknown-junction attempts must remain auditable too.
CREATE TABLE audit_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    junction_id TEXT,
    event_type TEXT NOT NULL,
    event_id TEXT,
    timestamp TEXT NOT NULL,
    details_json TEXT NOT NULL CHECK (json_valid(details_json))
);
CREATE INDEX history_by_junction ON audit_history(junction_id, id DESC);
