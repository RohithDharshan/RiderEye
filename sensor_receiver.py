import socket
import json
import threading
import time
import math
import requests

class SensorReceiver:
    def __init__(self, mode="udp", ip="0.0.0.0", port=5555, phone_ip="172.20.10.1"):
        self.mode = mode.lower()
        self.ip = ip
        self.port = port
        self.phone_ip = phone_ip # For Phyphox polling
        
        self.running = False
        self.latest_data = {
            "accel": [0, 0, 0],
            "gyro": [0, 0, 0],
            "tilt_angle": 0.0
        }
        self.thread = None
        
        if self.mode == "udp":
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind((self.ip, self.port))
            self.sock.settimeout(0.2)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.thread.start()
        if self.mode == "udp":
            print(f"SensorReceiver listening on UDP {self.ip}:{self.port}")
        else:
            print(f"SensorReceiver polling Phyphox at http://{self.phone_ip}:8080")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        if self.mode == "udp":
            self.sock.close()

    def _listen_loop(self):
        while self.running:
            if self.mode == "udp":
                self._process_udp()
            elif self.mode == "phyphox":
                self._process_phyphox()
                time.sleep(0.1) # Poll rate (10Hz)

    def _process_phyphox(self):
        try:
            # Phyphox "Acceleration with g" exposes accX, accY, accZ
            # URL: http://<ip>:8080/get?accX&accY&accZ
            url = f"http://{self.phone_ip}:8080/get?accX&accY&accZ"
            # print(f"Polling: {url}") # Debug
            
            # Increased timeout to 3.0s for better stability on hotspots
            response = requests.get(url, timeout=3.0)
            data = response.json()
            # print(f"Raw Data: {data}") # Debug
            
            # Parse Phyphox format: {"buffer": {"accX": {"buffer": [val]}, ...}}
            if "buffer" in data:
                buf = data["buffer"]
                # Check if keys exist
                if "accX" not in buf:
                    print(f"Error: 'accX' not found in Phyphox data. Keys: {buf.keys()}")
                    return

                ax = buf.get("accX", {}).get("buffer", [0])[0]
                ay = buf.get("accY", {}).get("buffer", [0])[0]
                az = buf.get("accZ", {}).get("buffer", [0])[0]
                
                # print(f"Accel: {ax}, {ay}, {az}") # Debug
                
                self.latest_data["accel"] = [ax, ay, az]
                self._update_tilt(ax, ay, az)
                
        except Exception as e:
            print(f"Phyphox poll error: {e}")
            pass

    def _process_udp(self):
        try:
            data, addr = self.sock.recvfrom(1024)
            text_data = data.decode('utf-8').strip()
            try:
                parsed = json.loads(text_data)
                accel = [0, 0, 0]
                if 'accelerometer' in parsed: accel = parsed['accelerometer']
                elif 'accel' in parsed: accel = parsed['accel']
                elif 'acceleration' in parsed: accel = parsed['acceleration']
                
                self.latest_data["accel"] = accel
                self._update_tilt(accel[0], accel[1], accel[2])
                
            except json.JSONDecodeError:
                pass
        except socket.timeout:
            pass
        except Exception as e:
            print(f"Error receiving UDP data: {e}")

    def _update_tilt(self, ax, ay, az):
        # Calculate roll (tilt around X-axis)
        # math.atan2(y, z) gives angle in radians
        # Note: Phyphox axes might differ based on phone orientation.
        # Assuming standard portrait/landscape.
        try:
            tilt_rad = math.atan2(ay, az) 
            tilt_deg = math.degrees(tilt_rad)
            self.latest_data["tilt_angle"] = tilt_deg
        except:
            pass

    def get_data(self):
        return self.latest_data
