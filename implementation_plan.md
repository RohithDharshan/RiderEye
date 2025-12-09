# RiderEye Web Dashboard Implementation Plan

## Goal Description
Create a "RiderEye Connect" web dashboard to visualize driving performance, real-time safety status, and incident history. This will serve as a companion app for the rider (or a post-ride analytics tool).

## User Review Required
> [!NOTE]
> **Architecture**: The `rider_eye.py` script will act as a client, sending data to a local Flask web server (`dashboard.py`) running on port 5000. You will need to run both scripts simultaneously.

## Proposed Changes

### 1. New Dependencies
- `flask`: For the web server.
- `requests`: Already added, used to send data to the dashboard.

### 2. Web Backend (`dashboard.py`)
- **Framework**: Flask.
- **Endpoints**:
    - `/`: Main dashboard view.
    - `/update` (POST): Receives real-time data (Status, Distance, Tilt, Safety Score) from `rider_eye.py`.
    - `/api/data` (GET): Returns the latest data for the frontend to poll.
- **Data Storage**: In-memory list of "Incidents" (times when safety was compromised) and a running "Safety Score".

### 3. Web Frontend
- **Design**: Dark mode, "Cyberpunk/Automotive" aesthetic (Neon Green/Red).
- **Components**:
    - **Live Status Card**: Shows current status (SAFE / WARNING / COLLISION).
    - **Safety Score Gauge**: 0-100% score based on driving behavior.
    - **Incident Log**: List of recent warnings with timestamps.
    - **Tilt/Distance Charts**: Simple visual indicators.

### 4. Integration (`rider_eye.py`)
- Modify the main loop to send a JSON payload to `http://localhost:5000/update` every ~500ms (to avoid overwhelming the server).
- Payload structure:
    ```json
    {
        "status": "SAFE",
        "distance": 150,
        "tilt": 0.5,
        "timestamp": "..."
    }
    ```

## Verification Plan
1.  **Start Dashboard**: Run `python dashboard.py`.
2.  **Start RiderEye**: Run `python rider_eye.py`.
3.  **Open Browser**: Go to `http://localhost:5000`.
4.  **Test**:
    - Trigger a "Collision" state with the camera.
    - Verify the Dashboard updates to "COLLISION" (Red).
    - Verify the "Safety Score" drops.
    - Verify the incident is logged in the "Recent Incidents" list.
