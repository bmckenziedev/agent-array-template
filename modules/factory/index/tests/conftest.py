import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest
from estate_index.tokens import default_counter

@pytest.fixture
def counter():
    return default_counter()
