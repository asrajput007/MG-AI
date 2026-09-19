import ssl
from celery import Celery
from helpers.utils import REDIS_URL, REDIS_CA_CERT_PATH_MG

import ssl

ssl_options = {
    "ssl_cert_reqs": ssl.CERT_REQUIRED,
    "ssl_ca_certs": REDIS_CA_CERT_PATH_MG,
    "ssl_check_hostname": True,
}


celery_app = Celery(
    "worker",
    broker=REDIS_URL,
    backend=REDIS_URL
)

celery_app.conf.update(
    broker_use_ssl=ssl_options,
    redis_backend_use_ssl=ssl_options,
    timezone = "UTC",
    enable_utc = True,
    broker_transport_options={
        "socket_timeout": 60,
        "socket_connect_timeout": 60,
    },
    result_backend_transport_options={
        "socket_timeout": 60,
        "socket_connect_timeout": 60,
        "health_check_interval": 30,
    }
)

celery_app.autodiscover_tasks(["tasks"])
celery_app.conf.task_ignore_result = True