"""Deterministic, portable benchmark workloads and their result oracle."""

from __future__ import annotations

import hashlib
from collections.abc import Callable

WORKLOAD_VERSION = "v1"

_MASK_64 = (1 << 64) - 1
_MIX_MULTIPLIER_1 = 0xBF58476D1CE4E5B9
_MIX_MULTIPLIER_2 = 0x94D049BB133111EB
_MIX_SEED = 0x9E3779B97F4A7C15
_DURATION_CLASSES = ("short", "medium", "long")
_DURATION_ROUNDS = {"short": 1, "medium": 8, "long": 32}


def worker_identity(payload: bytes) -> bytes:
    """Return a payload unchanged for backend scheduling-overhead controls."""
    return payload


def run_workload(name: str, payload: bytes) -> bytes:
    """Run one named workload and return its self-validating result frame."""
    try:
        compute_value = _WORKLOADS[name]
    except KeyError as error:
        raise ValueError(f"unknown workload: {name}") from error
    return _result_frame(name, payload, compute_value(payload))


def expected_result(name: str, payload: bytes) -> bytes:
    """Return the deterministic oracle result for one workload input."""
    try:
        compute_value = _REFERENCE_WORKLOADS[name]
    except KeyError as error:
        raise ValueError(f"unknown workload: {name}") from error
    return _reference_result_frame(name, payload, compute_value(payload))


def _result_frame(name: str, payload: bytes, computed_value: str) -> bytes:
    input_digest = hashlib.sha256(payload).hexdigest()
    prefix = "|".join((WORKLOAD_VERSION, name, input_digest, computed_value))
    output_digest = hashlib.sha256(prefix.encode("ascii")).hexdigest()
    return f"{prefix}|{output_digest}".encode("ascii")


def _reference_result_frame(name: str, payload: bytes, computed_value: str) -> bytes:
    input_digest = hashlib.sha256(payload).hexdigest()
    prefix = f"{WORKLOAD_VERSION}|{name}|{input_digest}|{computed_value}"
    output_digest = hashlib.sha256(prefix.encode("ascii")).hexdigest()
    return f"{prefix}|{output_digest}".encode("ascii")


def _trivial(_: bytes) -> str:
    return "schedule-control"


def _python_cpu(payload: bytes) -> str:
    board_size = 8 + (sum(payload) % 2)
    return f"nqueens:{board_size}:{_count_n_queens(board_size)}"


def _count_n_queens(board_size: int) -> int:
    def place(
        row: int,
        columns: int,
        descending_diagonals: int,
        ascending_diagonals: int,
    ) -> int:
        if row == board_size:
            return 1

        solutions = 0
        for column in range(board_size):
            descending = row - column + board_size - 1
            ascending = row + column
            if (
                columns & (1 << column)
                or descending_diagonals & (1 << descending)
                or ascending_diagonals & (1 << ascending)
            ):
                continue
            solutions += place(
                row + 1,
                columns | (1 << column),
                descending_diagonals | (1 << descending),
                ascending_diagonals | (1 << ascending),
            )
        return solutions

    return place(0, 0, 0, 0)


def _native_cpu(payload: bytes) -> str:
    return f"mix64:{_mix_64(payload):016x}"


def _payload_echo(payload: bytes) -> str:
    return f"length:{len(payload)}"


def _mixed_duration(payload: bytes) -> str:
    duration = _DURATION_CLASSES[sum(payload) % len(_DURATION_CLASSES)]
    mixed = _mix_64(payload, rounds=_DURATION_ROUNDS[duration])
    return f"duration:{duration}:mix64:{mixed:016x}"


def _mix_64(payload: bytes, *, rounds: int = 1) -> int:
    state = _MIX_SEED
    for round_index in range(rounds):
        for value in payload:
            state ^= value + round_index
            state = (state * _MIX_MULTIPLIER_1) & _MASK_64
            state ^= state >> 31
            state = (state * _MIX_MULTIPLIER_2) & _MASK_64
            state ^= state >> 27
    return state


def _reference_trivial(_: bytes) -> str:
    return "schedule-control"


def _reference_python_cpu(payload: bytes) -> str:
    payload_total = 0
    for value in payload:
        payload_total += value
    board_size = 8 + (payload_total % 2)
    return f"nqueens:{board_size}:{_reference_count_n_queens(board_size)}"


def _reference_count_n_queens(board_size: int) -> int:
    def place(row: int, columns: int, down_diagonals: int, up_diagonals: int) -> int:
        if row == board_size:
            return 1

        count = 0
        for column in range(board_size):
            down_bit = 1 << (row - column + board_size - 1)
            up_bit = 1 << (row + column)
            column_bit = 1 << column
            if columns & column_bit or down_diagonals & down_bit or up_diagonals & up_bit:
                continue
            count += place(
                row + 1,
                columns | column_bit,
                down_diagonals | down_bit,
                up_diagonals | up_bit,
            )
        return count

    return place(0, 0, 0, 0)


def _reference_native_cpu(payload: bytes) -> str:
    return f"mix64:{_reference_mix_64(payload):016x}"


def _reference_payload_echo(payload: bytes) -> str:
    length = 0
    for _ in payload:
        length += 1
    return f"length:{length}"


def _reference_mixed_duration(payload: bytes) -> str:
    payload_total = 0
    for value in payload:
        payload_total += value
    duration_index = payload_total % 3
    if duration_index == 0:
        duration, rounds = "short", 1
    elif duration_index == 1:
        duration, rounds = "medium", 8
    else:
        duration, rounds = "long", 32
    mixed = _reference_mix_64(payload, rounds=rounds)
    return f"duration:{duration}:mix64:{mixed:016x}"


def _reference_mix_64(payload: bytes, *, rounds: int = 1) -> int:
    state = 0x9E3779B97F4A7C15
    for round_index in range(rounds):
        for value in payload:
            state ^= value + round_index
            state = (state * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
            state ^= state >> 31
            state = (state * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
            state ^= state >> 27
    return state


_WORKLOADS: dict[str, Callable[[bytes], str]] = {
    "trivial": _trivial,
    "python_cpu": _python_cpu,
    "native_cpu": _native_cpu,
    "payload_echo": _payload_echo,
    "mixed_duration": _mixed_duration,
}

_REFERENCE_WORKLOADS: dict[str, Callable[[bytes], str]] = {
    "trivial": _reference_trivial,
    "python_cpu": _reference_python_cpu,
    "native_cpu": _reference_native_cpu,
    "payload_echo": _reference_payload_echo,
    "mixed_duration": _reference_mixed_duration,
}
