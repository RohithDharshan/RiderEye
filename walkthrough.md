# RiderEye Prototype Walkthrough

This guide will help you run and test the RiderEye prototype.

## Prerequisites
- **Laptop**: With webcam and Python installed (dependencies are already installed).
- **Smartphone**: With a sensor streaming app installed.
- **Wi-Fi**: Both devices must be on the same network.

## Step 1: Configure Smartphone App
1.  **Download App**:
    - **Android**: "SensorServer" or "IMU+GPS Stream".
    - **iOS**: "Sensor Log" or "Phyphox".
2.  **Settings**:
    - **Protocol**: UDP.
    - **IP Address**: Find your laptop's local IP (e.g., `192.168.1.X`).
        - Mac: System Settings > Wi-Fi > Details...
    - **Port**: `5555`.
    - **Data**: Enable **Accelerometer** and **Gyroscope**.
3.  **Start Streaming**: Turn on the stream.

## Step 2: Run the Application
1.  Open your terminal.
2.  Navigate to the project directory:
    ```bash
    cd /Users/mrohithdharshan/Downloads/Projects/RiderEye
    ```
3.  **Start the Dashboard** (New!):
    Open a new terminal tab/window and run:
    ```bash
    python dashboard.py
    ```
    Open your browser to [http://localhost:5000](http://localhost:5000).

4.  **Start RiderEye**:
    In your original terminal, run:
    ```bash
    python rider_eye.py
    ```
    *(Note: Use `python` not `python3` if you are using the environment where we installed dependencies)*

## Step 3: Verify & Calibrate
1.  **Check Console**: You should see "System Ready" and potentially "SensorReceiver listening...".
2.  **Check Camera**: The webcam feed should appear with a dashboard overlay.
3.  **Test Tilt**:
    - Tilt your phone left/right.
    - Watch the "Tilt" value on the screen. It should change.
    - If it stays at 0.0, check your IP/Port settings and ensure the phone is streaming.
4.  **Calibrate Distance**:
    - Place a car (or picture of a car) exactly **1.8 meters** (or a known distance) away.
    - If the distance on screen is wrong, adjust `FOCAL_LENGTH_CONST` in `rider_eye.py`.
    - Formula: `New_Focal_Length = (Known_Distance_cm * Pixel_Width) / Real_Width_cm`.

## Step 4: Simulate Scenarios
- **Collision Warning**: Move the car picture close (< 1.5m). Screen should flash **RED**.
- **Swerve Override**: While close, tilt the phone > 15 degrees. Screen should turn **ORANGE** ("SWERVE DETECTED").

## Troubleshooting
- **No Camera**: Ensure no other app is using the webcam.
- **No Sensor Data**:
    - Check firewall settings (allow UDP on port 5555).
    - Verify IP address hasn't changed.
    - Try a different sensor app if the JSON format is incompatible (check console for "Invalid JSON" errors if you uncomment the print statement in `sensor_receiver.py`).
