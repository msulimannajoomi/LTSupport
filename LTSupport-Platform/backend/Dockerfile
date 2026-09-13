FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ports are read from the environment (see config.py) rather than hardcoded, so they
# can be remapped freely at `docker run` time without rebuilding the image. These two
# are just the defaults if neither is overridden.
ENV LTSUPPORT_API_PORT=8000
ENV LTSUPPORT_RELAY_PORT=7000

EXPOSE 8000 7000

# LTSUPPORT_MONGODB_URI has no default (config.py requires it) and is never baked into
# this image -- copy .env to the server separately (see .env.example) and run with:
#   docker run -d --restart=always --env-file .env -p 8000:8000 -p 7000:7000 <image>
CMD ["python", "app.py"]
