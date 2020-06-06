import os
from pathlib import Path
import sys
import logging
import multiprocessing

multiprocessing.set_start_method("spawn")


# The windows tests have to use subprocesses, and it makes it easier
# if all tests are forced to have the package root as the CWD.
os.chdir(str(Path(__file__).parent.parent))
logging.basicConfig(level="DEBUG", stream=sys.stdout)
