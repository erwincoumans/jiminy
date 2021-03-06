###############################################################################
## @brief             Entry point for jiminy_pywrap python module.
###############################################################################

import os as _os # private import
import sys as _sys

if (_sys.version_info > (3, 0)):
    from contextlib import redirect_stderr as _redirect_stderr
    with open(_os.devnull, 'w') as stderr, _redirect_stderr(stderr):
        import pinocchio as _pnc # Preload the dynamic library Python binding if not already loaded
        from .libjiminy_pywrap import *
else:
    with open(_os.devnull, 'w') as stderr:
        old_target = _sys.stderr
        _sys.stderr = stderr

        import pinocchio as _pnc # Preload the dynamic library Python binding if not already loaded
        from .libjiminy_pywrap import *

        _sys.stderr = old_target

from .. import _pinocchio_init as _patch
