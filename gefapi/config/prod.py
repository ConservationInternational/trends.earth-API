import os

SETTINGS = {
    'logging': {
        'level': 'INFO'
    },
    'service': {
        'port': 3000
    },
    'environment': {
        'ROLLBAR_SCRIPT_TOKEN': os.getenv('ROLLBAR_SCRIPT_TOKEN'),
        'ROLLBAR_SERVER_TOKEN': os.getenv('ROLLBAR_SERVER_TOKEN'),
        'EE_PRIVATE_KEY': os.getenv('EE_PRIVATE_KEY'),
        'EE_SERVICE_ACCOUNT': os.getenv('EE_SERVICE_ACCOUNT'),
        'EE_SERVICE_ACCOUNT_JSON': os.getenv('EE_SERVICE_ACCOUNT_JSON')
    },
    'SCRIPTS_FS': '/data/scripts',
    'REGISTRY_URL': os.getenv('REGISTRY_URL'),
}
