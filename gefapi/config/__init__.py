"""GEFAPI CONFIG MODULE"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import os
from gefapi.config import base, develop, staging, prod

SETTINGS = base.SETTINGS

if os.getenv('ENVIRONMENT') == 'develop':
    SETTINGS.update(develop.SETTINGS)


if os.getenv('ENVIRONMENT') == 'staging':
    SETTINGS.update(staging.SETTINGS)


if os.getenv('ENVIRONMENT') == 'prod':
    SETTINGS.update(prod.SETTINGS)
