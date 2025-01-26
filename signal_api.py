from flask import Flask, request, jsonify
import subprocess

app = Flask(__name__)

@app.route('/send_message', methods=['POST'])
def send_message():
    data = request.get_json()

    # Extract message and recipient from the JSON body
    message = data.get('message')
    recipient = data.get('number')

    if not message or not recipient:
        return jsonify({"error": "Message and recipient are required"}), 400

    # Execute signal-cli command
    try:
        subprocess.run(['signal-cli', '-u', 'YOUR_PHONE_NUMBER', 'send', recipient, '-m', message], check=True)
        return jsonify({"status": "Message sent successfully"}), 200
    except subprocess.CalledProcessError as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, port=5000)
