import os

# Engine settings must be chosen before Pyroxide initializes.
os.environ.setdefault("PYROXIDE_SHM_THRESHOLD", "1048576")

from example_tasks import isolated_echo

if __name__ == "__main__":
    print("--- 7. Large Isolated Payload Example ---")
    payload = b"A" * (2 * 1024 * 1024)
    result = isolated_echo(payload).result()
    assert result == payload
    print(f"Round-tripped {len(result)} bytes through an isolated worker.")
    print(
        "Large serialized frames may use shared-memory routing; serialization and copies remain."
    )
