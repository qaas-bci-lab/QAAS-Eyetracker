"""Experiment-only acquisition entry point."""
import os
import sys

os.environ['ADHD_EYETRACK_EDITION'] = 'capture_raw'

if __name__ == '__main__':
    if '--self-check' in sys.argv:
        from pathlib import Path
        from raw_capture_check import run
        sys.exit(run(Path(sys.argv[sys.argv.index('--self-check') + 1])))
    from app import main
    main()
