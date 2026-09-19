import redis
import ssl
from helpers.utils import REDIS_URL, REDIS_CA_CERT_PATH_MG

ssl_options = {
    "ssl_cert_reqs": ssl.CERT_REQUIRED,
    "ssl_ca_certs": REDIS_CA_CERT_PATH_MG,
    "ssl_check_hostname": True,
}

try:
    client = redis.Redis.from_url(
        REDIS_URL,
        **ssl_options
    )

    print(REDIS_URL)
    print(REDIS_CA_CERT_PATH_MG)

    print("Pinging Redis...")
    response = client.ping()

    print("Ping response:", response)


    print(f"Connected successfully: {response}")

    client.set("test_key", "Hello Redis")
    value = client.get("test_key")

    print(f"Retrieved value: {value.decode()}")

except Exception as e:
    print(f"Connection failed:\n{e}")
