import os

SETTINGS = {
    'logging': {
        'level': 'INFO'
    },
    'service': {
        'port': 3000
    },
    'environment': {
        'EE_PRIVATE_KEY': os.getenv('EE_PRIVATE_KEY'),
        'EE_SERVICE_ACCOUNT': os.getenv('EE_SERVICE_ACCOUNT')
    },
    'SCRIPTS_FS': '/data/scripts',
    'REGISTRY_URL': os.getenv('REGISTRY_URL'),
}
