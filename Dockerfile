# SMS-compatible UPS -> NUT bridge
FROM python:3.12-slim

# Don't buffer stdout/stderr so logs show up immediately under docker logs.
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY sms_nut_bridge.py ./

# NUT network protocol
EXPOSE 3493

# Defaults can be overridden by appending args (or via compose `command:`).
ENTRYPOINT ["python3", "sms_nut_bridge.py"]
CMD ["--device", "/dev/ttyUSB0", "--host", "0.0.0.0", "--port", "3493", "--interval", "3"]
