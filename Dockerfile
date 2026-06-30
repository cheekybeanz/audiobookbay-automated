FROM python:3.10-slim
WORKDIR /app
COPY /app /app
RUN pip install --no-cache-dir -r /app/requirements.txt
# Create config directory for persistent data
RUN mkdir -p /config
EXPOSE 5078

# Healthcheck hits the app's own port to confirm Flask is actually responding,
# not just that the process is alive. Uses Python's built-in urllib instead of
# curl/wget since python:3.10-slim doesn't ship either by default. Reads PORT
# from the environment so it still works if the user overrides the default port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://localhost:{os.environ.get(\"PORT\", \"5078\")}/', timeout=3)" || exit 1

CMD ["python", "app.py"]
