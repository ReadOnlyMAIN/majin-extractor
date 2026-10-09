from .package import parse_motion_package
from .resource import UnsupportedMotion, discover_motion_paths
from .sequence import parse_motion_sequence

from .cli import main

import sys

if __name__ == "__main__":
    sys.exit(main())

