FROM python:3.10-slim
WORKDIR /app
COPY /app /app
RUN pip install --no-cache-dir -r /app/requirements.txt
# Create config directory for persistent data
RUN mkdir -p /config
EXPOSE 5078
CMD ["python", "app.py"]
