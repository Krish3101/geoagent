import logging

def setup_logging(log_level: str = "INFO"):
    level = getattr(logging, log_level.upper(), logging.INFO) if isinstance(log_level, str) else log_level
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler()
        ],
        force=True
    )
