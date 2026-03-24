FROM python:3.12-alpine

# curl is needed by the Ookla speedtest installer inside fetch.py
RUN apk add --no-cache curl

# Install Python dependencies up front so they are baked into the image
# and the runtime bootstrap in fetch.py becomes a fast no-op.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
CMD ["python", "fetch.py"]
