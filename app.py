import subprocess
import logging
import time
import threading
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import os
import requests

# Set up logging to print to the console and save to a file
logger = logging.getLogger("signal_cli")
logger.setLevel(logging.INFO)

# Create a console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)

# Create a file handler to save logs in the same project folder
log_file_path = os.path.join(os.path.dirname(__file__), "signal_cli_logs.txt")
file_handler = logging.FileHandler(log_file_path)
file_handler.setLevel(logging.INFO)

# Create a formatter and set it for both handlers
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
file_handler.setFormatter(formatter)

# Add both handlers to the logger
logger.addHandler(console_handler)
logger.addHandler(file_handler)

app = FastAPI()
templates = Jinja2Templates(directory="templates")

# Global variables to manage state
sending_active = False
stop_event = threading.Event()
stored_numbers = []
stored_message = ""
current_number = ""  # Global variable to track the current phone number being processed
global_delay = 0  # Renamed global variable to track delay between messages
linked_number = "+85265091684"  # Set your linked number here

# Lock to prevent concurrent access to signal-cli
signal_cli_lock = threading.Lock()

anti_captcha_api_key = "8de86a557b60c04fbc5f3c732ea7ac72"
signal_captcha_site_key = "6Lc6Xh0UAAAAAN-ZPPo8nr6Tq9WULHzXhINmYckY"  # Replace this with the correct site key


def remove_non_ascii_characters(text):
    return ''.join(char for char in text if ord(char) < 128)


def solve_captcha():
    try:
        url = "https://api.anti-captcha.com/createTask"
        data = {
            "clientKey": anti_captcha_api_key,
            "task": {
                "type": "NoCaptchaTaskProxyless",
                "websiteURL": "https://signalcaptchas.org/challenge/generate",
                "websiteKey": signal_captcha_site_key  # Replace with the correct site key
            }
        }
        response = requests.post(url, json=data)
        task_id = response.json().get("taskId")

        if task_id:
            logger.info(f"Captcha task created with ID: {task_id}")

            result_url = "https://api.anti-captcha.com/getTaskResult"
            while True:
                result_response = requests.post(result_url, json={"clientKey": anti_captcha_api_key, "taskId": task_id})
                result = result_response.json()
                if result.get("status") == "ready":
                    token = result["solution"]["gRecaptchaResponse"]
                    logger.info(f"Captcha solved successfully. Token: {token}")
                    return token
                time.sleep(5)
        else:
            logger.error(f"Failed to create captcha task. Response: {response.json()}")
            return None
    except Exception as e:
        logger.error(f"Exception occurred while solving captcha: {str(e)}")
        return None


def send_message_via_signal_cli(phone_number: str, message: str):
    try:
        retry_count = 0
        while retry_count < 5:
            with signal_cli_lock:
                # Remove non-ASCII characters
                message = remove_non_ascii_characters(message)

                # Write the message to a temporary file with UTF-8 encoding
                with open("message.txt", "w", encoding="utf-8") as f:
                    f.write(message)

                # Use signal-cli without the full path, assuming it's in the system's PATH
                command = f'type message.txt | signal-cli -u {linked_number} send {phone_number} --message-from-stdin'
                logger.info(f"Executing command: {command}")
                result = subprocess.run(command, capture_output=True, text=True, shell=True)

                if result.returncode == 0:
                    logger.info(f"Message sent successfully to {phone_number}")
                    return True, "Success"
                else:
                    error_message = result.stderr.strip()
                    logger.error(f"Failed to send message to {phone_number}. Error: {error_message}")
                    if "rate limiting" in error_message:
                        token = solve_captcha()
                        if token:
                            challenge_command = f'signal-cli -a {linked_number} submitRateLimitChallenge --challenge 5fad97ac-7d06-4e44-b18a-b950b20148ff --captcha {token}'
                            logger.info(f"Executing challenge command: {challenge_command}")
                            challenge_result = subprocess.run(challenge_command, capture_output=True, text=True,
                                                              shell=True)
                            if challenge_result.returncode == 0:
                                logger.info(f"Rate limit challenge submitted successfully for {phone_number}")
                                # Retry sending the message
                                result = subprocess.run(command, capture_output=True, text=True, shell=True)
                                if result.returncode == 0:
                                    logger.info(
                                        f"Message sent successfully to {phone_number} after rate limit challenge")
                                    return True, "Success after challenge"
                                else:
                                    logger.error(
                                        f"Failed to send message to {phone_number} after rate limit challenge. Error: {result.stderr.strip()}")
                            else:
                                logger.error(
                                    f"Failed to submit rate limit challenge. Error: {challenge_result.stderr.strip()}")
                        else:
                            return False, "Failed to solve captcha"
                    retry_count += 1
                    time.sleep(5)  # Wait before retrying
        return False, error_message
    except Exception as e:
        logger.error(f"Exception occurred while sending message to {phone_number}. Exception: {str(e)}")
        return False, str(e)


def send_sms_background():
    global sending_active, current_number, global_delay
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
        time.sleep(global_delay)  # Use the global delay specified in the front end
    current_number = ""  # Clear current number after processing
    sending_active = False
    event_source.put("event: close")


@app.post("/send-sms")
async def send_sms(numbers: str = Form(...), message: str = Form(...), delay: int = Form(...)):
    global stored_numbers, stored_message, stop_event, sending_active, global_delay
    stored_numbers = [num.strip() for num in numbers.split(",") if num.strip()]
    stored_message = message
    global_delay = int(delay)

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
    event_source.put("event: close")
    return HTMLResponse(content="SMS sending stopped.", status_code=200)


@app.get("/send-sms-stream")
async def stream_sms_results():
    def event_generator():
        while sending_active and not stop_event.is_set():
            while not event_source.queue.empty():
                yield f"data: {event_source.queue.get()}\n\n"
            time.sleep(1)  # Small sleep to prevent excessive CPU usage
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
    global sending_active, current_number
    status = "Sending active" if sending_active else "No message being sent currently."
    return {"status": status, "current_number": current_number}


@app.get("/", response_class=HTMLResponse)
async def form(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


def poll_for_incoming_messages():
    while True:
        try:
            with signal_cli_lock:
                # Use signal-cli without the full path, assuming it's in the system's PATH
                command = f'signal-cli -u {linked_number} receive'
                result = subprocess.run(command, capture_output=True, text=True, shell=True)
                if result.returncode == 0:
                    logger.info("Successfully polled for incoming messages.")
                else:
                    logger.error(f"Failed to poll for incoming messages. Error: {result.stderr.strip()}")
        except Exception as e:
            logger.error(f"Exception occurred while polling for incoming messages: {str(e)}")

        time.sleep(10)  # Adjust the interval as needed


# Start polling in a background thread
threading.Thread(target=poll_for_incoming_messages)

if __name__ == "__main__":
    import uvicorn

    logger.info("Starting the FastAPI application...")
    uvicorn.run(app, host="0.0.0.0", port=8000)
