"""Stable command definitions shared by keyboard launchers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Command:
    command_id: str
    name: str
    aliases: tuple[str, ...] = ()


class CommandRegistry:
    """Small ordered registry; handlers stay owned by the application."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._handlers: dict[str, Callable[[], None]] = {}

    def register(self, command: Command, handler: Callable[[], None]) -> None:
        if command.command_id in self._commands:
            raise ValueError(f"duplicate command id: {command.command_id}")
        self._commands[command.command_id] = command
        self._handlers[command.command_id] = handler

    @property
    def commands(self) -> tuple[Command, ...]:
        return tuple(self._commands.values())

    def execute(self, command_id: str) -> bool:
        handler = self._handlers.get(command_id)
        if handler is None:
            return False
        handler()
        return True
