import cv2
import math
import time
import requests
import threading
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
    filtered_distance = None
    target_seen_frames = 0
    connection_status = "Checking..."
    connection_color = (200, 200, 200)

    try:
        while True:
            ret, frame = cap.read()
            if not ret: break

            # Get latest sensor data
            sensor_data = sensors.get_data()
            phone_tilt_angle = sensor_data["tilt_angle"]
            
            # Check if data is stale (sensor not running)
            # We can check if the values are exactly 0,0,0 for a long time, 
            # but for now let's just assume if we get data it's "Connected"
            # In a real app we'd track the timestamp of the last packet.
            if sensors.latest_data["accel"] == [0,0,0]:
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
            
            # Default overlay info
            cv2.putText(frame, f"Tilt: {phone_tilt_angle:.1f} deg", (10, 70), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            
            # Connection Status
            cv2.putText(frame, connection_status, (10, 100), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, connection_color, 1)

            collision_imminent = False
            swerve_detected = False
            warning_obstacle = False
            candidate_vehicles = []

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

                    if not in_center_zone:
                        continue

                    vehicle_dist_cm = calculate_distance(box_w)
                    candidate_vehicles.append(
                        {
                            "distance": vehicle_dist_cm,
                            "conf": conf,
                            "label": cls_name,
                            "x1": int(x1),
                            "y1": int(y1),
                            "x2": int(x2),
                            "y2": int(y2),
                        }
                    )

            if candidate_vehicles:
                # Pick nearest in-center vehicle as threat target.
                target = min(candidate_vehicles, key=lambda item: item["distance"])
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
