import argparse
import sys
from pyroxide import _pyroxide

def main():
    parser = argparse.ArgumentParser(description="Pyroxide IPC Worker Process")
    parser.add_argument("--socket", required=True, help="Path to the Unix Domain Socket or Named Pipe")
    args = parser.parse_args()

    try:
        # Start the Rust-native high-performance IPC loop
        _pyroxide.start_worker_loop(args.socket)
    except Exception as e:
        print(f"Pyroxide worker process crashed: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
