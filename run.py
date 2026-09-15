"""Запуск: python run.py  (добавь --no-browser чтобы не открывать вкладку)"""
import sys
import threading
import webbrowser

import uvicorn

HOST, PORT = "127.0.0.1", 8765

if __name__ == "__main__":
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://{HOST}:{PORT}")).start()
    uvicorn.run("app.main:app", host=HOST, port=PORT, log_level="warning")
