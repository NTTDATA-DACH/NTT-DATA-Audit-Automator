# Stage 1: Use an official Python runtime as a parent image
# Using a "slim" image to keep the final image size down.
FROM python:3.11-slim-bookworm

# Set environment variables
# 1. Prevents Python from writing .pyc files to disk
# 2. Prevents Python from buffering stdout and stderr, which is good for logging
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Install system-level dependencies required for our Python packages.
# `poppler-utils` is required by `unstructured[pdf]`.
# We combine update, install, and cleanup into one RUN command to reduce image layers.
RUN apt-get update && \
    apt-get install -y --no-install-recommends poppler-utils && \
    rm -rf /var/lib/apt/lists/*

# Copy the requirements file into the container
# This is done before copying the rest of the code to leverage Docker's layer caching.
# This layer will only be rebuilt if requirements.txt changes.
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# --no-cache-dir reduces image size by not storing the download cache.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's source code into the container
COPY . .

# Specify the command to run on container start.
# This makes the container executable and allows us to pass arguments like `--run-etl`
# when we run the container.
ENTRYPOINT ["python", "main.py"]

# Set a default command to show the help message if no other command is provided.
CMD ["--help"]