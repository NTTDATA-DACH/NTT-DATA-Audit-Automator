# Stage 1: Use an official Python runtime as a parent image
# Using a "slim" image to keep the final image size down.
FROM python:3.11-slim-bookworm

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1

# Set the working directory in the container
WORKDIR /app

# Add the non-root user's local bin to the PATH.
# This prevents warnings during pip install.
ENV PATH="/app/.local/bin:${PATH}"

# It's good practice to upgrade pip to the latest version
RUN pip install --upgrade pip

# Create a non-root user and group to run the application
# This avoids the "Running pip as root" warning and is a security best practice.
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin -c "Docker image user" appuser
RUN chown -R appuser:appgroup /app

# Copy the requirements file into the container
# This is done before copying the rest of the code to leverage Docker's layer caching.
COPY requirements.txt .

# Switch to the non-root user before installing dependencies
USER appuser

# Install any needed packages specified in requirements.txt
# --no-cache-dir reduces image size by not storing the download cache.
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application's source code into the container
COPY . .

# Specify the command to run on container start.
ENTRYPOINT ["python", "main.py"]

# Set a default command to show the help message if no other command is provided.
CMD ["--help"]