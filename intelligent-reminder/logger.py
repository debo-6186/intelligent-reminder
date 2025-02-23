import logging
import sys
from logging.handlers import RotatingFileHandler
import os

# Get the absolute path to the project root directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Create logs directory using absolute path
logs_dir = "/app/logs"  # Use absolute path in Docker container
if not os.path.exists(logs_dir):
    os.makedirs(logs_dir, exist_ok=True)

# Configure logger
logger = logging.getLogger("reminder_app")
logger.setLevel(logging.INFO)

# Format for the logs
formatter = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Console Handler (for all logs)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# File Handler for all logs
file_handler = RotatingFileHandler(
    os.path.join(logs_dir, "app.log"),
    maxBytes=10485760,  # 10MB
    backupCount=5
)
file_handler.setLevel(logging.INFO)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)