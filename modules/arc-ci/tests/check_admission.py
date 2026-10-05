from pathlib import Path
import runpy
globals().update(runpy.run_path(str(Path(__file__).parent / 'live/check_admission.py')))
