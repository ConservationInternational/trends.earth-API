
import os
from datetime import timedelta

SETTINGS = {
    'logging': {
        'level': 'DEBUG'
    },
    'service': {
        'port': 3000
    },
    'environment': {
        'EE_PRIVATE_KEY': os.getenv('EE_PRIVATE_KEY'),
        'EE_SERVICE_ACCOUNT': os.getenv('EE_SERVICE_ACCOUNT'),
        'SPARKPOST_API_KEY': os.getenv('SPARKPOST_API_KEY'),
        'API_URL': os.getenv('API_URL'),
        'API_USER': os.getenv('API_USER'),
        'API_PASSWORD': os.getenv('API_PASSWORD')
    },
    'ROLES': ['ADMIN', 'USER'],
    'SQLALCHEMY_DATABASE_URI': 'postgresql://'+os.getenv('DATABASE_ENV_POSTGRES_USER')+':'+os.getenv('DATABASE_ENV_POSTGRES_PASSWORD')+'@'+os.getenv('DATABASE_PORT_5432_TCP_ADDR')+':'+os.getenv('DATABASE_PORT_5432_TCP_PORT')+'/'+os.getenv('DATABASE_ENV_POSTGRES_DB'),
    'SECRET_KEY': 'mysecret',
    'DOCKER_URL': os.getenv('DOCKER_URL'),
    'REGISTRY_URL': 'localhost:'+os.getenv('REGISTRY_PORT_5000_TCP_PORT', ''),
    'UPLOAD_FOLDER': '/tmp/scripts',
    'ALLOWED_EXTENSIONS': set(['tar.gz']),
    'JWT_AUTH_USERNAME_KEY': 'email',
    'JWT_AUTH_HEADER_PREFIX': 'Bearer',
    'JWT_EXPIRATION_DELTA': timedelta(seconds=60*60*24),
    'SCRIPTS_FS': '/data/scripts',
    'CELERY_BROKER_URL': 'redis://'+os.getenv('REDIS_PORT_6379_TCP_ADDR')+':' + os.getenv('REDIS_PORT_6379_TCP_PORT'),
    'CELERY_RESULT_BACKEND':'redis://'+os.getenv('REDIS_PORT_6379_TCP_ADDR')+':' + os.getenv('REDIS_PORT_6379_TCP_PORT')
}
