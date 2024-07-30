import os

if os.getenv("ENVIRONMENT") == "prod":
    # instance_ip = urllib.request.urlopen(
    #    'http://169.254.169.254/latest/meta-data/local-ipv4').read().decode()

    SETTINGS = {
        "logging": {"level": "INFO"},
        #'CELERY_BROKER_URL': 'redis://' + instance_ip + ':' + os.getenv('REDIS_PORT_6379_TCP_PORT'),
        #'CELERY_RESULT_BACKEND': 'redis://' + instance_ip + ':' + os.getenv('REDIS_PORT_6379_TCP_PORT')
        "CELERY_BROKER_URL": "redis://"
        + os.getenv("REDIS_PORT_6379_TCP_ADDR")
        + ":"
        + os.getenv("REDIS_PORT_6379_TCP_PORT"),
        "CELERY_RESULT_BACKEND": "redis://"
        + os.getenv("REDIS_PORT_6379_TCP_ADDR")
        + ":"
        + os.getenv("REDIS_PORT_6379_TCP_PORT"),
    }
