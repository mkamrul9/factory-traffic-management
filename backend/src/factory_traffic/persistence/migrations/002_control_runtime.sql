ALTER TABLE sensor_event_receipts RENAME TO event_receipts;

CREATE TABLE junction_runtime (
    junction_id TEXT PRIMARY KEY REFERENCES junctions(junction_id),
    state_json TEXT NOT NULL CHECK (json_valid(state_json))
);

CREATE TABLE controller_commands (
    command_id TEXT PRIMARY KEY,
    junction_id TEXT NOT NULL REFERENCES junctions(junction_id),
    batch_id TEXT NOT NULL,
    epoch TEXT NOT NULL,
    signal_version INTEGER NOT NULL CHECK (signal_version > 0),
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
