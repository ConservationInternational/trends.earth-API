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
        'ROLLBAR_SCRIPT_TOKEN': os.getenv('ROLLBAR_SCRIPT_TOKEN'),
        'ROLLBAR_SERVER_TOKEN': os.getenv('ROLLBAR_SERVER_TOKEN'),
        'EE_SERVICE_ACCOUNT_JSON': os.getenv('EE_SERVICE_ACCOUNT_JSON'),
        'SPARKPOST_API_KEY': os.getenv('SPARKPOST_API_KEY'),
        'API_URL': os.getenv('API_URL'),
        'API_USER': os.getenv('API_USER'),
        'API_PASSWORD': os.getenv('API_PASSWORD'),
        'PARAMS_S3_PREFIX': os.getenv('PARAMS_S3_PREFIX'),
        'PARAMS_S3_BUCKET': os.getenv('PARAMS_S3_BUCKET')
    },
    'ROLES': ['ADMIN', 'USER'],
    'SQLALCHEMY_DATABASE_URI':
    'postgresql://' + os.getenv('DATABASE_ENV_POSTGRES_USER') + ':' +
    os.getenv('DATABASE_ENV_POSTGRES_PASSWORD') + '@' +
    os.getenv('DATABASE_PORT_5432_TCP_ADDR') + ':' +
    os.getenv('DATABASE_PORT_5432_TCP_PORT') + '/' +
    os.getenv('DATABASE_ENV_POSTGRES_DB'),
    'SECRET_KEY':
    'mysecret',
    'DOCKER_HOST':
    os.getenv('DOCKER_HOST'),
    'REGISTRY_URL':
    os.getenv('REGISTRY_URL'),
    'SCRIPTS_S3_PREFIX':
    os.getenv('SCRIPTS_S3_PREFIX'),
    'SCRIPTS_S3_BUCKET':
    os.getenv('SCRIPTS_S3_BUCKET'),
    'PARAMS_S3_PREFIX':
    os.getenv('PARAMS_S3_PREFIX'),
    'PARAMS_S3_BUCKET':
    os.getenv('PARAMS_S3_BUCKET'),
    'UPLOAD_FOLDER':
    '/tmp/scripts',
    'ALLOWED_EXTENSIONS':
    set(['tar.gz']),
    'JWT_AUTH_USERNAME_KEY':
    'email',
    'JWT_AUTH_HEADER_PREFIX':
    'Bearer',
    'JWT_EXPIRATION_DELTA':
    timedelta(seconds=60 * 60 * 24),
    'CELERY_BROKER_URL':
    'redis://' + os.getenv('REDIS_PORT_6379_TCP_ADDR') + ':' +
    os.getenv('REDIS_PORT_6379_TCP_PORT'),
    'CELERY_RESULT_BACKEND':
    'redis://' + os.getenv('REDIS_PORT_6379_TCP_ADDR') + ':' +
    os.getenv('REDIS_PORT_6379_TCP_PORT')
}
