import os

SETTINGS = {
    'logging': {
        'level': 'DEBUG'
    },
    'service': {
        'port': 3000
    },
    'REGISTRY_URL': os.getenv('REGISTRY_URL'),
}
