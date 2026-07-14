import asyncio
import threading

_loop = None

def get_loop():
    global _loop
    if _loop is None:
        _loop = asyncio.new_event_loop()
        def start():
            asyncio.set_event_loop(_loop)
            _loop.run_forever()
        threading.Thread(target=start, daemon=True).start()
    return _loop
