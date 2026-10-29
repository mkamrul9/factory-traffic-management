from collections.abc import Sequence
from datetime import datetime

from factory_traffic.domain.controller import (
    AcknowledgementStatus,
    ControllerAcknowledgement,
    ControllerCommand,
)


class RestControllerSimulator:
    """Explicitly simulate executing committed commands, never assume physical execution."""

    def execute(
        self, commands: Sequence[ControllerCommand], now: datetime
    ) -> tuple[ControllerAcknowledgement, ...]:
        return tuple(
            ControllerAcknowledgement(
                command.command_id,
                command.junction_id,
                AcknowledgementStatus.ACK,
                command.requested_state,
                now,
            )
            for command in commands
        )
