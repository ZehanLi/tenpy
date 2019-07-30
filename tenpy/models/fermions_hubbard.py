"""The contents of this module have been moved to :mod:`tenpy.models.hubbard`.

This module is just around for backwards compatibility."""
# Copyright 2019 TeNPy Developers

from .hubbard import FermionicHubbardModel, FermionicHubbardChain

import warnings

msg = """RESTRUCTURING
***********
* WARNING:
* The "bose_hubbard.py" and "fermions_hubbard.py" models have now been consolidated into "hubbard.py".
***********
To avoid this warning, simply import the model class from `tenpy.models.hubbard` instead of `tenpy.models.fermions_hubbard`."""
warnings.warn(msg)
warnings.warn("The module `tenpy.models.fermions_hubbard` is deprecated now.", FutureWarning)