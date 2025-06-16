import os
from dataclasses import dataclass
import pandas as pd
from loaders.data_loader import load_camera_data

@dataclass
class AllCamerasInfo:
    cam_ids: list
    metas: list[pd.DataFrame]
    frames: list[list]
    timestamps: list[list]
    tracknets: list[pd.DataFrame | None]
    synced_groups: list
    max_frames: int
    frame_idx: int
    rotation_angles: list
    playing: bool
    brightness_factor: float


# Build synced groups based on timestamps from multiple cameras
def build_synced_groups(timestamps: list[list], cam_nums: int, sync_tol: float = 0.004166) -> list[tuple[float, list[int | None]]]:

    sync = []
    pointers = [0] * cam_nums
    while True:
        # collect candidates from all cameras, each candidate is a tuple of (timestamp of a frame, camera index)
        candidates = [(timestamps[i][pointers[i]], i) 
                      for i in range(cam_nums) 
                      if pointers[i] < len(timestamps[i])]
        # all the frames are used
        if not candidates:
            break
        
        # use the minimum timestamp of the candidates as the reference time
        ref_time, _ = min(candidates)
        group = []
        used = False

        for cam in range(cam_nums):
            # get the timestamp list for the current camera
            ts_list = timestamps[cam]

            # check if the current timestamp of the camera is too old, fix the pointer if so
            while pointers[cam] < len(ts_list) and ts_list[pointers[cam]] < ref_time - sync_tol:
                pointers[cam] += 1

            # if the current timestamp is within the sync tolerance, add it to the group
            if pointers[cam] < len(ts_list) and abs(ts_list[pointers[cam]] - ref_time) <= sync_tol:
                group.append(pointers[cam])
                used = True
                pointers[cam] += 1
            else:
                group.append(None)
        if used:
            sync.append((ref_time, group))

    return sync

# Load data for all cameras and build synced groups
def load_all_cameras_data(files_path: str, syc_tol: float = 0.004166) -> AllCamerasInfo:
    
    meta_files = [f for f in os.listdir(files_path)
                      if f.startswith('CameraReader_') and f.endswith('_meta.csv')]
    cam_ids = sorted(int(f.split('_')[1]) for f in meta_files)[:4]
    num_cams = len(cam_ids)

    all_metas = []
    all_frames = []
    all_timestamps = []
    all_tracknets = []
  
    # Load data for each camera
    for cam_id in cam_ids:
        
        cam_info = load_camera_data(files_path, cam_id)
        all_metas.append(cam_info.meta_df)
        all_frames.append(cam_info.frame_buf)
        all_timestamps.append(cam_info.meta_df['timestamp'].tolist())
        all_tracknets.append(cam_info.tracknet_df)
    
    # Get the synced groups and max frames
    synced_groups = build_synced_groups(all_timestamps, num_cams, syc_tol)
    max_frames = len(synced_groups)
    rotation_angles = [0] * num_cams
    playing = False
    brightness_factor = 1.0

    return AllCamerasInfo(
        cam_ids=cam_ids,
        metas=all_metas,
        frames=all_frames,
        timestamps=all_timestamps,
        tracknets=all_tracknets,
        synced_groups=synced_groups,
        max_frames=max_frames,
        frame_idx=0,
        rotation_angles=rotation_angles,
        playing=playing,
        brightness_factor=brightness_factor
    )
