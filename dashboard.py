from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
from datetime import datetime
import time
from collections import deque
import os

app = Flask(__name__)
CORS(app)

# In-memory storage
current_status = {
    "status": "OFFLINE",
    "distance": 0,
    "tilt": 0.0,
    "alert_active": False,
    "slowdown_showcase": 0,
    "target_conf": 0.0,
    "target_label": "none",
    "vehicle_count": 0,
    "two_wheeler_count": 0,
    "nearby_vehicles": [],
    "speed_kmh": 0.0,
    "distance_travelled_km": 0.0,
    "heading_deg": 0.0,
    "location": {"lat": 0.0, "lon": 0.0},
    "weather": {
        "condition": "Unknown",
        "temperature_c": 0.0,
        "precipitation_mm": 0.0,
        "wind_kmh": 0.0,
    },
    "road_condition": {
        "label": "Unknown",
        "risk": "Low",
        "roughness_index": 0.0,
    },
    "safety_score": 100,
    "timestamp": None
}

incidents = []
# Limit incidents to last 10
MAX_INCIDENTS = 10
speed_samples = deque(maxlen=300)
trend_history = deque(maxlen=120)

demo_state = {
    "mode": "normal",
    "distance_offset_km": 0.0,
}

analytics = {
    "trip_start": datetime.now().strftime("%H:%M:%S"),
    "max_speed_kmh": 0.0,
    "avg_speed_kmh": 0.0,
    "moving_time_sec": 0,
    "alerts_total": 0,
    "warnings_total": 0,
    "collisions_total": 0,
    "traffic_state": "Stable",
}

last_update_ts = None
last_status_type = "SAFE"

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/update', methods=['POST'])
def update_data():
    global current_status, last_update_ts, last_status_type
    data = request.json or {}

    # Update current status
    current_status.update(data)

    raw_distance_km = float(current_status.get("distance_travelled_km", 0.0) or 0.0)
    adjusted_distance_km = max(0.0, raw_distance_km - demo_state["distance_offset_km"])
    current_status["raw_distance_travelled_km"] = round(raw_distance_km, 3)
    current_status["distance_travelled_km"] = round(adjusted_distance_km, 3)

    current_status["timestamp"] = datetime.now().strftime("%H:%M:%S")

    status_text = str(data.get("status", "SAFE"))
    now_ts = time.time()

    dt = 0
    if last_update_ts is not None:
        dt = max(0, now_ts - last_update_ts)
    last_update_ts = now_ts

    speed_kmh = float(current_status.get("speed_kmh", 0.0) or 0.0)
    speed_samples.append(speed_kmh)
    analytics["avg_speed_kmh"] = round(sum(speed_samples) / len(speed_samples), 2) if speed_samples else 0.0
    analytics["max_speed_kmh"] = round(max(analytics["max_speed_kmh"], speed_kmh), 2)
    if speed_kmh > 2:
        analytics["moving_time_sec"] += int(round(dt))

    current_status_type = "SAFE"
    if "COLLISION" in status_text:
        current_status_type = "COLLISION"
    elif "WARNING" in status_text:
        current_status_type = "WARNING"

    mode = demo_state["mode"]
    risk_multiplier = 1.0 if mode == "normal" else 1.35

    if mode == "aggressive" and current_status_type == "SAFE" and int(current_status.get("vehicle_count", 0) or 0) >= 3:
        analytics["traffic_state"] = "Dense"
    else:
        analytics["traffic_state"] = "Stable"

    if "COLLISION" in status_text:
        current_status["safety_score"] = max(0, current_status["safety_score"] - (5 * risk_multiplier))
        _log_incident("COLLISION", "Collision Alert + Slowdown Showcase")

    elif "WARNING" in status_text:
        current_status["safety_score"] = max(0, current_status["safety_score"] - (1 * risk_multiplier))
        incident_msg = "Swerve Detected" if "SWERVE" in status_text else "Obstacle Warning"
        _log_incident("WARNING", incident_msg)

    else:
        # Slowly recover score if safe
        current_status["safety_score"] = min(100, current_status["safety_score"] + 0.1)

    if current_status_type != "SAFE" and current_status_type != last_status_type:
        analytics["alerts_total"] += 1
        if current_status_type == "WARNING":
            analytics["warnings_total"] += 1
        elif current_status_type == "COLLISION":
            analytics["collisions_total"] += 1

    trend_history.append(
        {
            "t": current_status["timestamp"],
            "speed_kmh": round(speed_kmh, 2),
            "distance_km": round(current_status.get("distance_travelled_km", 0.0), 3),
            "roughness": round(float(current_status.get("road_condition", {}).get("roughness_index", 0.0) or 0.0), 3),
            "alerts_total": int(analytics["alerts_total"]),
        }
    )

    last_status_type = current_status_type

    return jsonify({"success": True})

@app.route('/api/data')
def get_data():
    clean_incidents = [
        {
            "type": item["type"],
            "message": item["message"],
            "time": item["time"],
        }
        for item in incidents
    ]

    return jsonify({
        "status": current_status,
        "incidents": clean_incidents,
        "analytics": analytics,
        "controls": {
            "demo_mode": demo_state["mode"],
        },
        "trends": {
            "speed_kmh": [item["speed_kmh"] for item in trend_history],
            "distance_km": [item["distance_km"] for item in trend_history],
            "roughness": [item["roughness"] for item in trend_history],
            "alerts_total": [item["alerts_total"] for item in trend_history],
        },
    })

@app.route('/api/control', methods=['POST'])
def control_panel():
    payload = request.json or {}
    action = str(payload.get("action", "")).strip().lower()

    if action == "set_mode":
        mode = str(payload.get("mode", "normal")).strip().lower()
        if mode not in {"normal", "aggressive"}:
            return jsonify({"success": False, "error": "Invalid mode"}), 400
        demo_state["mode"] = mode
        return jsonify({"success": True, "mode": mode})

    if action == "reset_trip":
        demo_state["distance_offset_km"] = float(current_status.get("raw_distance_travelled_km", current_status.get("distance_travelled_km", 0.0)) or 0.0)
        current_status["distance_travelled_km"] = 0.0
        current_status["safety_score"] = 100

        analytics["trip_start"] = datetime.now().strftime("%H:%M:%S")
        analytics["max_speed_kmh"] = 0.0
        analytics["avg_speed_kmh"] = 0.0
        analytics["moving_time_sec"] = 0
        analytics["alerts_total"] = 0
        analytics["warnings_total"] = 0
        analytics["collisions_total"] = 0
        analytics["traffic_state"] = "Stable"

        incidents.clear()
        speed_samples.clear()
        trend_history.clear()
        return jsonify({"success": True})

    return jsonify({"success": False, "error": "Unknown action"}), 400

def _log_incident(type, message):
    # Avoid duplicate logging (debounce)
    if incidents and incidents[0]["message"] == message and \
       (time.time() - incidents[0]["_ts"]) < 2:
        return

    incidents.insert(0, {
        "type": type,
        "message": message,
        "time": datetime.now().strftime("%H:%M:%S"),
        "_ts": time.time(),
    })
    
    if len(incidents) > MAX_INCIDENTS:
        incidents.pop()

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5050))
    debug = os.environ.get('FLASK_ENV') != 'production'
    app.run(debug=debug, port=port, host='0.0.0.0')
