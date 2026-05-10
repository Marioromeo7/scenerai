import sys
from pathlib import Path

# Put backend/ on the path so tests can import engine.*, config, etc.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
