"""GEFAPI CONFIG MODULE"""

from __future__ import absolute_import, division, print_function

import collections.abc
import os

from gefapi.config import base, prod, staging


# Below is from https://stackoverflow.com/a/3233356. Needed to handle the "environment"
# key
def _nested_dict_update(d, u):
    for k, v in u.items():
        if isinstance(v, collections.abc.Mapping):
            d[k] = _nested_dict_update(d.get(k, {}), v)
        else:
            d[k] = v
    return d


SETTINGS = base.SETTINGS

if os.getenv("ENVIRONMENT") == "staging":
    _nested_dict_update(SETTINGS, staging.SETTINGS)


if os.getenv("ENVIRONMENT") == "prod":
    _nested_dict_update(SETTINGS, prod.SETTINGS)
