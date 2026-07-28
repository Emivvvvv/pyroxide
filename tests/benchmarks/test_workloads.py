import pytest

from examples.benchmarks import workloads
from examples.benchmarks.workloads import (
    expected_result,
    run_workload,
    worker_identity,
)


@pytest.mark.parametrize(
    ("name", "payload", "want"),
    [
        (
            "trivial",
            b"",
            b"v1|trivial|e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855|schedule-control|763ebe8f9e7785c21b03ce5fbea8c9ba1c7e718b8b38348be8278bf03d22a631",
        ),
        (
            "python_cpu",
            b"queens-8",
            b"v1|python_cpu|674114a61158a3041c2790622c776b7ad77ed5ad636a53791c09fbd8c12411dc|nqueens:8:92|192ae9cfca67d24f1aa5bdbd9312ff12f27c5e774ff9ede9c2a3fcba3c93b379",
        ),
        (
            "native_cpu",
            b"\x00\x01\x02\x03",
            b"v1|native_cpu|054edec1d0211f624fed0cbca9d4f9400b0e491c43742af2c5b0abebf0c990d8|mix64:50991a11a3a7aa90|60aaf59c49ae6207300d7f146770e8f44dafd9a94c2ea422045e72cc108c6e27",
        ),
        (
            "payload_echo",
            b"payload\\x00echo",
            b"v1|payload_echo|e65d18fbcc824dae5d0ddb515e4e28d2dc34bbcab69e4b20ca085112b7b979de|length:15|ec1572ecf557ebdc590a66fb292b7c8c655a6b7f3e2734249df3ec6e9e3e0a3e",
        ),
        (
            "mixed_duration",
            b"a",
            b"v1|mixed_duration|ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb|duration:medium:mix64:ba19d8e8b218f46b|3fce330e1415f5734fcf34ce108481c41d5556620a570f2f6837274dbc470cf8",
        ),
        (
            "mixed_duration",
            b"b",
            b"v1|mixed_duration|3e23e8160039594a33894f6564e1b1348bbd7a0088d42c4acb73eeaed59c009d|duration:long:mix64:4f75ef1e837b81cc|c4301e0f3930711b0c060d6490cbd1659dc761b3681d7a7bde7d31f266721112",
        ),
        (
            "mixed_duration",
            b"c",
            b"v1|mixed_duration|2e7d2c03a9507ae265ecf5b5356885a53393a2029d241394997265a1a25aefc6|duration:short:mix64:01f2ae8cd7d92b37|94433f40ce61624de01cc4b2e2d4ff2aeae0fcf5fef899ab6a5cdf099e13aab0",
        ),
    ],
)
def test_workloads_match_hand_derived_golden_vectors(
    name: str, payload: bytes, want: bytes
) -> None:
    """Changing a workload kernel, frame field, or digest calculation must fail."""
    assert run_workload(name, payload) == want
    assert expected_result(name, payload) == want


def test_worker_identity_preserves_binary_payload() -> None:
    """Changing the backend round-trip control to transform bytes must fail."""
    assert worker_identity(b"\x00worker\xff") == b"\x00worker\xff"


def test_reference_oracle_detects_a_corrupted_execution_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replacing an execution workload must not corrupt the expected-result oracle."""
    want = (
        b"v1|trivial|e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        b"|schedule-control|763ebe8f9e7785c21b03ce5fbea8c9ba1c7e718b8b38348be8278bf03d22a631"
    )
    monkeypatch.setitem(workloads._WORKLOADS, "trivial", lambda _: "corrupted")

    assert expected_result("trivial", b"") == want
    assert run_workload("trivial", b"") != expected_result("trivial", b"")


def test_workload_dispatch_rejects_unknown_names() -> None:
    """Changing an unknown workload into a silent fallback must fail."""
    with pytest.raises(ValueError, match="unknown workload"):
        run_workload("unknown", b"")
