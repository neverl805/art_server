@echo off
python -m pip install -r requirements.txt
python manage_logs.py sync
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log
