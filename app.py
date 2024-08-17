from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi import BackgroundTasks
from fastapi.templating import Jinja2Templates
import subprocess
import logging
import time
from typing import List
import threading
from fastapi import Request

# Set up logging to print to the console
logger = logging.getLogger("signal_cli")
logger.setLevel(logging.INFO)

# Create a console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Create a formatter and set it for the handler
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)

# Add the handler to the logger
logger.addHandler(console_handler)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Global variables to manage state
sending_active = False
stop_event = threading.Event()
stored_numbers = []
stored_message = ""
current_number = ""  # Global variable to track the current phone number being processed


def send_message_via_signal_cli(phone_number: str, message: str):
    try:
        # Adjust the command for macOS
        command = f'/usr/local/bin/signal-cli -u +923325278158 send -m "{message}" {phone_number}'
        logger.info(f"Executing command: {command}")
        result = subprocess.run(command, capture_output=True, text=True, shell=True)

        if result.returncode == 0:
            logger.info(f"Message sent successfully to {phone_number}")
            return True, "Success"
        else:
            logger.error(f"Failed to send message to {phone_number}. Error: {result.stderr.strip()}")
            return False, result.stderr.strip()
    except Exception as e:
        logger.error(f"Exception occurred while sending message to {phone_number}. Exception: {str(e)}")
        return False, str(e)

def send_sms_background():
    global sending_active, current_number
    sending_active = True
    for number in stored_numbers:
        if stop_event.is_set():
            logger.info(f"Stopping message sending. Finalizing status for {number}.")
            break
        current_number = number
        success, response = send_message_via_signal_cli(number, stored_message)
        status = "Sent" if success else "Failed"
        # Notify via streaming
        event_source.put(f"{number},{status},{response}")
        time.sleep(0.5)  # Simulate delay to show streaming behavior
    current_number = ""  # Clear current number after processing
    sending_active = False
    event_source.put("event: close")


@app.post("/send-sms")
async def send_sms(numbers: str = Form(...), message: str = Form(...)):
    global stored_numbers, stored_message, stop_event, sending_active
    stored_numbers = [num.strip() for num in numbers.split(",")]
    stored_message = message

    if sending_active:
        return HTMLResponse(content="SMS sending is already in progress.", status_code=400)

    stop_event.clear()
    threading.Thread(target=send_sms_background).start()

    return HTMLResponse(content="SMS sending started. Check the results at <a href='/'>here</a>.", status_code=200)


@app.post("/stop-sms")
async def stop_sms():
    global sending_active
    if not sending_active:
        return HTMLResponse(content="No SMS sending process is active.", status_code=400)

    stop_event.set()
    return HTMLResponse(content="SMS sending stopped.", status_code=200)


@app.get("/send-sms-stream")
async def stream_sms_results():
    def event_generator():
        while sending_active and not stop_event.is_set():
            time.sleep(0.1)  # Small sleep to prevent excessive CPU usage
        while not event_source.queue.empty():
            yield f"data: {event_source.queue.get()}\n\n"
        yield "event: close\n\n"  # Signal the end of the stream

    class EventSource:
        def __init__(self):
            from queue import Queue
            self.queue = Queue()

        def put(self, message):
            self.queue.put(message)

    global event_source
    event_source = EventSource()
    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/current-status")
async def get_current_status():
    global current_number
    return {"current_number": current_number}


@app.get("/", response_class=HTMLResponse)
async def form(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


if __name__ == "__main__":
    import uvicorn

    logger.info("Starting the FastAPI application...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
