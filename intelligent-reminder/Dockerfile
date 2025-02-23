# Use Python 3.9 slim image as base
FROM python:3.9-slim

# Set working directory in the container
WORKDIR /app

# Install system dependencies and poetry
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -sSL https://install.python-poetry.org | python3 -

# Add Poetry to PATH
ENV PATH="${PATH}:/root/.local/bin"

# Copy poetry files
COPY pyproject.toml poetry.lock* ./

# Configure poetry to not create virtual environment (as we're in container)
RUN poetry config virtualenvs.create false

# Install dependencies (only main dependencies, no dev dependencies)
RUN poetry install --only main --no-interaction --no-ansi

# Copy the application code
COPY . .

# Expose port 8000 (FastAPI default port)
EXPOSE 8000

# Command to run the application using uvicorn
CMD ["poetry", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]