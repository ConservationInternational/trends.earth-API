import os

SETTINGS = {
    'logging': {
        'level': 'INFO'
    },
    'service': {
        'port': 3000
    },
    'REGISTRY_URL': os.getenv('REGISTRY_URL'),
}
