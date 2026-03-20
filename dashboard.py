from flask import Flask, render_template, jsonify, request
from datetime import datetime

app = Flask(__name__)

# In-memory storage
current_status = {
    "status": "OFFLINE",
    "distance": 0,
    "tilt": 0.0,
    "alert_active": False,
    "slowdown_showcase": 0,
    "target_conf": 0.0,
    "target_label": "none",
    "safety_score": 100,
    "timestamp": None
}

incidents = []
# Limit incidents to last 10
MAX_INCIDENTS = 10

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/update', methods=['POST'])
def update_data():
    global current_status
    data = request.json or {}
    
    # Update current status
    current_status.update(data)
    current_status["timestamp"] = datetime.now().strftime("%H:%M:%S")
    
    # Logic for Safety Score & Incidents
    # If status is COLLISION, drop score significantly
    status_text = str(data.get("status", "SAFE"))

    if "COLLISION" in status_text:
        current_status["safety_score"] = max(0, current_status["safety_score"] - 5)
        _log_incident("COLLISION", "Collision Alert + Slowdown Showcase")
        
    elif "WARNING" in status_text:
        current_status["safety_score"] = max(0, current_status["safety_score"] - 1)
        incident_msg = "Swerve Detected" if "SWERVE" in status_text else "Obstacle Warning"
        _log_incident("WARNING", incident_msg)
        
    else:
        # Slowly recover score if safe
        current_status["safety_score"] = min(100, current_status["safety_score"] + 0.1)

    return jsonify({"success": True})

@app.route('/api/data')
def get_data():
    return jsonify({
        "status": current_status,
        "incidents": incidents
    })

def _log_incident(type, message):
    # Avoid duplicate logging (debounce)
    if incidents and incidents[0]["message"] == message and \
       (datetime.now() - incidents[0]["_dt"]).seconds < 2:
        return

    incidents.insert(0, {
        "type": type,
        "message": message,
        "time": datetime.now().strftime("%H:%M:%S"),
        "_dt": datetime.now()
    })
    
    if len(incidents) > MAX_INCIDENTS:
        incidents.pop()

if __name__ == '__main__':
    app.run(debug=True, port=5050)
