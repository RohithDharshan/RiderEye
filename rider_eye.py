import cv2
import math
import time
import requests
import threading
from ultralytics import YOLO
from sensor_receiver import SensorReceiver

# --- CONFIGURATION ---
SAFE_DISTANCE = 150  # Threshold in centimeters (1.5m for prototype)
FOCAL_LENGTH_CONST = 600 # You need to calibrate this value
REAL_WIDTH_CAR = 180 # cm (Approx width of a car)
PHONE_IP = "172.20.10.1" # Default Gateway IP for Hotspot (Phone's IP)
SENSOR_MODE = "phyphox" # Options: "udp" or "phyphox"
DASHBOARD_URL = "http://127.0.0.1:5000/update"

def calculate_distance(pixel_width):
    # D = (W * F) / P
    if pixel_width == 0: return 0
    return (REAL_WIDTH_CAR * FOCAL_LENGTH_CONST) / pixel_width

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
            # conf=0.15 is VERY LOW to detect cars inside phone screens
            results = model(frame, stream=True, verbose=False, conf=0.15)
            
            status = "SAFE"
            status_color = (0, 255, 0) # Green
            dist_cm = 0 
            
            # Default overlay info
            cv2.putText(frame, f"Tilt: {phone_tilt_angle:.1f} deg", (10, 70), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            
            # Connection Status
            cv2.putText(frame, connection_status, (10, 100), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, connection_color, 1)

            collision_imminent = False
            swerve_detected = False

            for r in results:
                boxes = r.boxes
                for box in boxes:
                    # Get Class ID
                    cls = int(box.cls[0])
                    # Get Class Name
                    cls_name = model.names[cls]
                    
                    # Get Box Coordinates
                    x1, y1, x2, y2 = box.xyxy[0]
                    pixel_width = x2 - x1
                    
                    # Debug: Draw ALL detected objects in Gray first
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (100, 100, 100), 1)
                    cv2.putText(frame, f"{cls_name}", (int(x1), int(y1)-5), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (100, 100, 100), 1)

                    # Check if detected object is a vehicle (Class IDs: 2=Car, 3=Motorcycle, 5=Bus, 7=Truck)
                    if cls in [2, 3, 5, 7]: 
                        
                        # 2. DISTANCE CALCULATION
                        dist_cm = calculate_distance(float(pixel_width))
                        
                        # Draw Box (Bright Color)
                        cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (255, 255, 0), 2)
                        cv2.putText(frame, f"{cls_name} {int(dist_cm)} cm", (int(x1), int(y1)-10), 
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 2)

                        # 3. ACTIVE COLLISION LOGIC
                        if dist_cm < SAFE_DISTANCE:
                            # Check lean angle (absolute value)
                            if abs(phone_tilt_angle) < 15: 
                                collision_imminent = True
                            else:
                                swerve_detected = True

            # Determine final status based on all detections
            if collision_imminent:
                status = "COLLISION IMMINENT - THROTTLE CUT!"
                status_color = (0, 0, 255) # Red
            elif swerve_detected:
                status = "WARNING - SWERVE DETECTED"
                status_color = (0, 165, 255) # Orange

            # 4. DASHBOARD INTERFACE (Local)
            cv2.rectangle(frame, (0, 0), (640, 90), (0, 0, 0), -1) 
            cv2.putText(frame, f"STATUS: {status}", (10, 35), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, status_color, 2)
            cv2.putText(frame, f"Tilt: {phone_tilt_angle:.1f}", (10, 75), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (200, 200, 200), 1)

            if status == "COLLISION IMMINENT - THROTTLE CUT!":
                cv2.rectangle(frame, (0,0), (frame.shape[1], frame.shape[0]), (0,0,255), 20)

            cv2.imshow('RiderEye Prototype', frame)
            
            # 5. SEND TO WEB DASHBOARD (Throttled to 2Hz)
            if time.time() - last_dashboard_update > 0.5:
                payload = {
                    "status": status,
                    "distance": dist_cm,
                    "tilt": phone_tilt_angle
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
