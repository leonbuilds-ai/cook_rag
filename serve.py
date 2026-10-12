"""Launch exactly one worker: sessions and the local model queue live in this process."""
import uvicorn
from logging_config import configure_logging
from service_config import load_service_config


if __name__ == '__main__':
    settings = load_service_config()
    configure_logging(settings)
    uvicorn.run('api:app', host=settings.host, port=settings.port, workers=1,
                log_config=None, access_log=False, limit_concurrency=64)
