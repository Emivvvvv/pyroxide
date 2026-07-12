import os
import sys

# Ensure the PYROXIDE_WORKERS is set before pyroxide is imported.
# Default to 4 workers for concurrency tests.
os.environ.setdefault("PYROXIDE_WORKERS", "4")

# Add the workspace/python directory to sys.path so pytest can find pyroxide
sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "python"))
)
