import os
import urllib.request

# Only try to load the instance IP when actually running in production
if os.getenv('ENVIRONMENT') == 'prod':
    instance_ip = urllib.request.urlopen(
        'http://169.254.169.254/latest/meta-data/local-ipv4').read().decode()
else:
    instance_ip = 'localhost'

SETTINGS = {
    'logging': {
        'level': 'INFO'
    },
    'service': {
        'port': 3000
    },
    'REGISTRY_URL':
    os.getenv('REGISTRY_URL'),
    'CELERY_BROKER_URL':
    'redis://' + instance_ip + ':' + os.getenv('REDIS_PORT_6379_TCP_PORT'),
    'CELERY_RESULT_BACKEND':
    'redis://' + instance_ip + ':' + os.getenv('REDIS_PORT_6379_TCP_PORT')
}
