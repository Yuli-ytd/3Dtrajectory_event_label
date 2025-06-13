from dataclasses import dataclass
import pandas as pd
import os
import cv2

@dataclass
class CameraInfo:
    cam_id: int
    meta_df: pd.DataFrame # DataFrame containing metadata for the camera
    frame_buf:list # List of frames for the camera
    tracknet_df: pd.DataFrame # DataFrame containing TrackNet data points of the camera

# Function to load camera data from a specified folder
def load_camera_data(file_path: str, cam_id: int) -> CameraInfo | None:
    
    try:
        meta = pd.read_csv(os.path.join(file_path, f"CameraReader_{cam_id}_meta.csv"))
        
        cap = cv2.VideoCapture(os.path.join(file_path, f"CameraReader_{cam_id}.mp4"))
        frames = []
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frames.append(frame)
        cap.release()

        tracknet_path = os.path.join(file_path, f"TrackNet_{cam_id}.csv")
        tracknet = pd.read_csv(tracknet_path).set_index('Frame') if os.path.exists(tracknet_path) else None
    
        return CameraInfo(cam_id, meta, frames, tracknet)
    
    except FileNotFoundError as e:
        raise FileNotFoundError(f"Could not find the required files for camera {cam_id} in {file_path}.") from e
    except pd.errors.EmptyDataError as e:
        raise ValueError(f"Metadata file for camera {cam_id} is empty or malformed.") from e