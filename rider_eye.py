import cv2
import math
import time
import requests
import threading
from collections import deque
from ultralytics import YOLO
from sensor_receiver import SensorReceiver

# --- CONFIGURATION ---
SAFE_DISTANCE_CM = 210  # Warning zone threshold (increased for phone-camera blur)
DANGER_DISTANCE_CM = 150  # Critical zone threshold
HARD_BRAKE_DISTANCE_CM = 105  # Strong slowdown showcase zone
FOCAL_LENGTH_CONST = 600 # You need to calibrate this value
REAL_WIDTH_CAR = 180 # cm (Approx width of a car)
PHONE_IP = "172.20.10.1" # Default Gateway IP for Hotspot (Phone's IP)
SENSOR_MODE = "phyphox" # Options: "udp" or "phyphox"
DASHBOARD_URL = "http://127.0.0.1:5050/update"
MIN_VEHICLE_CONF = 0.35
MIN_TWO_WHEELER_CONF = 0.30
TILT_SWERVE_THRESHOLD = 15
TILT_STRAIGHT_THRESHOLD = 12
CENTER_ZONE_RATIO = 0.55
EMA_ALPHA = 0.35
DETECTION_PERSIST_FRAMES = 4
ALERT_COOLDOWN_SEC = 1.5
VEHICLE_CLASS_IDS = {1, 2, 3, 5, 7}
TWO_WHEELER_CLASS_IDS = {1, 3}
WEATHER_REFRESH_SEC = 300
START_LAT = 12.9716
START_LON = 77.5946
CRUISE_SPEED_KMH = 32.0
MAX_SPEED_KMH = 85.0
ROAD_ROUGHNESS_WINDOW = 35

WEATHER_CODE_MAP = {
    0: "Clear",
    1: "Mostly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    80: "Rain showers",
    81: "Heavy showers",
    95: "Thunderstorm",
}

def clamp(value, min_val, max_val):
    return max(min_val, min(max_val, value))

def calculate_distance(pixel_width):
    # D = (W * F) / P
    if pixel_width == 0: return 0
    return (REAL_WIDTH_CAR * FOCAL_LENGTH_CONST) / pixel_width

def calculate_slowdown_showcase(distance_cm):
    # Showcase-only value that simulates ECU slowdown command (0-100%).
    if distance_cm <= 0:
        return 0
    if distance_cm <= HARD_BRAKE_DISTANCE_CM:
        return 80
    if distance_cm <= DANGER_DISTANCE_CM:
        return 65
    if distance_cm <= SAFE_DISTANCE_CM:
        # Linear ramp from 35% to 65% as distance gets closer.
        ratio = (SAFE_DISTANCE_CM - distance_cm) / max(1, SAFE_DISTANCE_CM - DANGER_DISTANCE_CM)
        return int(35 + (ratio * 30))
    return 0

def accel_magnitude_g(ax, ay, az):
    magnitude = math.sqrt((ax * ax) + (ay * ay) + (az * az))
    # If phone is reporting m/s^2, convert approximately to g.
    if magnitude > 4:
        return magnitude / 9.81
    return magnitude

def estimate_speed_kmh(prev_speed, ax, dt, slowdown_showcase, sensor_ok):
    speed = prev_speed

    if sensor_ok:
        accel_gain = 18.0
        speed += ax * dt * accel_gain
    else:
        # Drift to a stable cruise speed in demo mode.
        speed += (CRUISE_SPEED_KMH - speed) * min(1.0, dt * 0.45)

    # Showcase slowdown effect from obstacle logic.
    speed -= slowdown_showcase * dt * 0.07

    # Natural rolling drag.
    speed -= speed * dt * 0.025
    return clamp(speed, 0.0, MAX_SPEED_KMH)

def destination_point(lat, lon, distance_m, bearing_deg):
    if distance_m <= 0:
        return lat, lon

    earth_radius_m = 6371000
    bearing = math.radians(bearing_deg)
    lat1 = math.radians(lat)
    lon1 = math.radians(lon)
    angular_distance = distance_m / earth_radius_m

    lat2 = math.asin(
        math.sin(lat1) * math.cos(angular_distance)
        + math.cos(lat1) * math.sin(angular_distance) * math.cos(bearing)
    )
    lon2 = lon1 + math.atan2(
        math.sin(bearing) * math.sin(angular_distance) * math.cos(lat1),
        math.cos(angular_distance) - math.sin(lat1) * math.sin(lat2),
    )

    return math.degrees(lat2), math.degrees(lon2)

def fetch_weather_snapshot(lat, lon):
    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "current": "temperature_2m,weather_code,precipitation,wind_speed_10m",
                "timezone": "auto",
            },
            timeout=2.5,
        )
        payload = response.json().get("current", {})
        code = int(payload.get("weather_code", 0))
        return {
            "source": "open-meteo",
            "condition": WEATHER_CODE_MAP.get(code, "Unknown"),
            "temperature_c": float(payload.get("temperature_2m", 0.0)),
            "precipitation_mm": float(payload.get("precipitation", 0.0)),
            "wind_kmh": float(payload.get("wind_speed_10m", 0.0)),
            "weather_code": code,
        }
    except Exception:
        return None

def classify_road_condition(roughness_index, precipitation_mm, speed_kmh):
    label = "Smooth"
    risk = "Low"

    if roughness_index > 0.35:
        label = "Rough"
        risk = "Medium"
    elif roughness_index > 0.20:
        label = "Moderate"
        risk = "Low"

    if precipitation_mm > 0.2:
        label = f"{label} + Wet"
        risk = "High" if speed_kmh > 35 else "Medium"

    return {
        "label": label,
        "risk": risk,
        "roughness_index": round(roughness_index, 3),
    }

def send_dashboard_update(data):
    try:
        requests.post(DASHBOARD_URL, json=data, timeout=0.1)
    except:
        pass

def main():
    print("Initializing RiderEye...")
    
    # 1. Start Sensor Receiver
    sensors = SensorReceiver(mode=SENSOR_MODE, phone_ip=PHONE_IP)
    sensors.start()
    
    # 2. Load AI Model
    print("Loading YOLOv8 model (Small)...")
    # Using 'yolov8s.pt' (Small) for better accuracy at distance
    model = YOLO('yolov8s.pt') 
    
    # 3. Open Camera
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Error: Could not open webcam.")
        return

    print("System Ready. Press 'q' to exit.")
    
    last_dashboard_update = 0
    last_alert_time = 0
    last_weather_fetch = 0
    last_loop_time = time.time()
    filtered_distance = None
    target_seen_frames = 0
    speed_kmh = 0.0
    distance_travelled_km = 0.0
    latitude = START_LAT
    longitude = START_LON
    heading_deg = 82.0
    roughness_samples = deque(maxlen=ROAD_ROUGHNESS_WINDOW)
    weather_snapshot = {
        "source": "fallback",
        "condition": "Unknown",
        "temperature_c": 0.0,
        "precipitation_mm": 0.0,
        "wind_kmh": 0.0,
        "weather_code": 0,
    }
    connection_status = "Checking..."
    connection_color = (200, 200, 200)

    try:
        while True:
            ret, frame = cap.read()
            if not ret: break

            now = time.time()
            dt = min(0.2, max(0.01, now - last_loop_time))
            last_loop_time = now

            # Get latest sensor data
            sensor_data = sensors.get_data()
            phone_tilt_angle = sensor_data["tilt_angle"]
            accel = sensor_data.get("accel", [0, 0, 0])
            ax = float(accel[0]) if len(accel) > 0 else 0.0
            ay = float(accel[1]) if len(accel) > 1 else 0.0
            az = float(accel[2]) if len(accel) > 2 else 0.0
            
            # Check if data is stale (sensor not running)
            sensor_ok = sensors.latest_data["accel"] != [0, 0, 0]
            if not sensor_ok:
                connection_status = "Phone: NO DATA (Press Play?)"
                connection_color = (0, 0, 255)
            else:
                connection_status = "Phone: Connected"
                connection_color = (0, 255, 0)

            # 1. AI DETECTION
            # Use stronger confidence and target selection to reduce false positives.
            results = model(frame, verbose=False, conf=MIN_VEHICLE_CONF, iou=0.5, imgsz=960)
            
            status = "SAFE"
            status_color = (0, 255, 0) # Green
            dist_cm = 0
            alert_active = False
            slowdown_showcase = 0
            target_conf = 0.0
            target_label = "none"
            vehicle_count = 0
            two_wheeler_count = 0
            nearby_vehicles = []
            
            # Default overlay info
            cv2.putText(frame, f"Tilt: {phone_tilt_angle:.1f} deg", (10, 70), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            
            # Connection Status
            cv2.putText(frame, connection_status, (10, 100), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, connection_color, 1)

            collision_imminent = False
            swerve_detected = False
            warning_obstacle = False
            center_lane_candidates = []

            _, frame_w = frame.shape[:2]
            center_x = frame_w / 2
            center_half_span = (frame_w * CENTER_ZONE_RATIO) / 2

            if results:
                r = results[0]
                boxes = r.boxes
                for box in boxes:
                    cls = int(box.cls[0])
                    conf = float(box.conf[0])
                    cls_name = model.names[cls]
                    x1, y1, x2, y2 = box.xyxy[0]

                    box_w = float(x2 - x1)
                    box_h = float(y2 - y1)
                    if box_w <= 1 or box_h <= 1:
                        continue

                    box_center_x = float((x1 + x2) / 2)
                    in_center_zone = abs(box_center_x - center_x) <= center_half_span

                    # Draw all detections in dim gray for debugging.
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (100, 100, 100), 1)
                    cv2.putText(
                        frame,
                        f"{cls_name} {conf:.2f}",
                        (int(x1), int(y1) - 5),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.4,
                        (120, 120, 120),
                        1,
                    )

                    if cls not in VEHICLE_CLASS_IDS:
                        continue

                    # Two-wheelers are allowed at a slightly lower confidence.
                    min_conf_threshold = MIN_TWO_WHEELER_CONF if cls in TWO_WHEELER_CLASS_IDS else MIN_VEHICLE_CONF
                    if conf < min_conf_threshold:
                        continue

                    vehicle_dist_cm = calculate_distance(box_w)
                    lane = "center" if in_center_zone else "side"

                    vehicle_count += 1
                    if cls in TWO_WHEELER_CLASS_IDS:
                        two_wheeler_count += 1

                    det_item = {
                        "distance": vehicle_dist_cm,
                        "conf": conf,
                        "label": cls_name,
                        "x1": int(x1),
                        "y1": int(y1),
                        "x2": int(x2),
                        "y2": int(y2),
                        "lane": lane,
                    }
                    nearby_vehicles.append(det_item)

                    det_color = (255, 170, 60) if lane == "center" else (180, 140, 40)
                    cv2.rectangle(frame, (det_item["x1"], det_item["y1"]), (det_item["x2"], det_item["y2"]), det_color, 2)
                    cv2.putText(
                        frame,
                        f"{cls_name} {int(vehicle_dist_cm)}cm",
                        (det_item["x1"], max(16, det_item["y1"] - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        det_color,
                        2,
                    )

                    if in_center_zone:
                        center_lane_candidates.append(det_item)

            nearby_vehicles.sort(key=lambda item: item["distance"])

            if center_lane_candidates:
                # Pick nearest in-center vehicle as threat target.
                target = min(center_lane_candidates, key=lambda item: item["distance"])
                raw_distance = target["distance"]

                if filtered_distance is None:
                    filtered_distance = raw_distance
                else:
                    filtered_distance = (EMA_ALPHA * raw_distance) + ((1 - EMA_ALPHA) * filtered_distance)

                target_seen_frames = min(target_seen_frames + 1, 100)
                dist_cm = filtered_distance
                target_conf = target["conf"]
                target_label = target["label"]

                threat_color = (0, 255, 255)
                if dist_cm < DANGER_DISTANCE_CM:
                    threat_color = (0, 0, 255)
                elif dist_cm < SAFE_DISTANCE_CM:
                    threat_color = (0, 165, 255)

                cv2.rectangle(
                    frame,
                    (target["x1"], target["y1"]),
                    (target["x2"], target["y2"]),
                    threat_color,
                    2,
                )
                cv2.putText(
                    frame,
                    f"TARGET: {target_label} {int(dist_cm)} cm ({target_conf:.2f})",
                    (target["x1"], max(20, target["y1"] - 10)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    threat_color,
                    2,
                )
            else:
                target_seen_frames = max(0, target_seen_frames - 1)
                if target_seen_frames == 0:
                    filtered_distance = None

            # 3. ACTIVE COLLISION/ALERT LOGIC
            if target_seen_frames >= DETECTION_PERSIST_FRAMES and dist_cm > 0:
                if dist_cm < DANGER_DISTANCE_CM and abs(phone_tilt_angle) < TILT_STRAIGHT_THRESHOLD:
                    collision_imminent = True
                elif dist_cm < SAFE_DISTANCE_CM and abs(phone_tilt_angle) >= TILT_SWERVE_THRESHOLD:
                    swerve_detected = True
                elif dist_cm < SAFE_DISTANCE_CM:
                    warning_obstacle = True

                slowdown_showcase = calculate_slowdown_showcase(dist_cm)

            speed_kmh = estimate_speed_kmh(speed_kmh, ax, dt, slowdown_showcase, sensor_ok)
            distance_travelled_km += (speed_kmh * dt) / 3600

            heading_deg = (heading_deg + (phone_tilt_angle * 0.015 * dt)) % 360
            latitude, longitude = destination_point(latitude, longitude, (speed_kmh / 3.6) * dt, heading_deg)

            roughness_samples.append(abs(accel_magnitude_g(ax, ay, az) - 1.0))
            roughness_index = sum(roughness_samples) / len(roughness_samples) if roughness_samples else 0.0

            if now - last_weather_fetch > WEATHER_REFRESH_SEC:
                weather_data = fetch_weather_snapshot(latitude, longitude)
                if weather_data:
                    weather_snapshot = weather_data
                last_weather_fetch = now

            road_condition = classify_road_condition(
                roughness_index,
                weather_snapshot.get("precipitation_mm", 0.0),
                speed_kmh,
            )

            # Determine final status based on all detections
            if collision_imminent:
                status = "COLLISION IMMINENT - ALERT + SLOWDOWN SHOWCASE"
                status_color = (0, 0, 255) # Red
            elif warning_obstacle:
                status = "WARNING - OBSTACLE AHEAD"
                status_color = (0, 165, 255) # Orange
            elif swerve_detected:
                status = "WARNING - SWERVE DETECTED"
                status_color = (0, 165, 255) # Orange

            alert_active = collision_imminent
            if collision_imminent and (time.time() - last_alert_time > ALERT_COOLDOWN_SEC):
                last_alert_time = time.time()

            # 4. DASHBOARD INTERFACE (Local)
            cv2.rectangle(frame, (0, 0), (640, 90), (0, 0, 0), -1) 
            cv2.putText(frame, f"STATUS: {status}", (10, 35), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)
            cv2.putText(frame, f"Tilt: {phone_tilt_angle:.1f}", (10, 75), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

            cv2.putText(
                frame,
                f"Showcase Slowdown: {slowdown_showcase}%",
                (10, 115),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (255, 255, 255),
                1,
            )

            cv2.putText(
                frame,
                f"Speed: {speed_kmh:.1f} km/h  Distance: {distance_travelled_km:.2f} km",
                (10, 145),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (220, 220, 220),
                1,
            )

            cv2.putText(
                frame,
                f"Vehicles: {vehicle_count} (2W: {two_wheeler_count})  Road: {road_condition['label']}",
                (10, 172),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.52,
                (220, 220, 220),
                1,
            )

            if "COLLISION" in status:
                cv2.rectangle(frame, (0,0), (frame.shape[1], frame.shape[0]), (0,0,255), 20)

            cv2.imshow('RiderEye Prototype', frame)
            
            # 5. SEND TO WEB DASHBOARD (Throttled to 2Hz)
            if time.time() - last_dashboard_update > 0.5:
                payload = {
                    "status": status,
                    "distance": dist_cm,
                    "tilt": phone_tilt_angle,
                    "alert_active": alert_active,
                    "slowdown_showcase": slowdown_showcase,
                    "target_conf": target_conf,
                    "target_label": target_label,
                    "vehicle_count": vehicle_count,
                    "two_wheeler_count": two_wheeler_count,
                    "nearby_vehicles": [
                        {
                            "label": item["label"],
                            "distance_cm": round(item["distance"], 1),
                            "confidence": round(item["conf"], 2),
                            "lane": item["lane"],
                        }
                        for item in nearby_vehicles[:6]
                    ],
                    "speed_kmh": round(speed_kmh, 2),
                    "distance_travelled_km": round(distance_travelled_km, 3),
                    "location": {
                        "lat": round(latitude, 6),
                        "lon": round(longitude, 6),
                    },
                    "weather": weather_snapshot,
                    "road_condition": road_condition,
                }
                # Send in background thread to avoid blocking UI
                threading.Thread(target=send_dashboard_update, args=(payload,), daemon=True).start()
                last_dashboard_update = time.time()

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
    except KeyboardInterrupt:
        pass
    finally:
        print("Shutting down...")
        sensors.stop()
        cap.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
