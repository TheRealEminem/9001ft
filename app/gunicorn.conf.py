from os import environ

# https://docs.gunicorn.org/en/stable/settings.html#settings
bind = f"0.0.0.0:{environ.get('PORT', 5000)}"

# Threads keep the live progress stream from blocking other requests, and
# let a slow AI tidy answer finish without the worker being restarted.
threads = 4
timeout = 300
