# analyzes a single shot (serve, backhand, or forehand) and compares it to a pro player

# features:
# compares average wrist velocity
# compares joint angles (knees, wrist, shoulder)
# comparison: map of wrist location (swing path), track using coordinates plotted of path, mediapipe to locate wrist

import cv2 as cv
import numpy as np
import mediapipe as mp
from ultralytics import YOLO
import os
import uuid

class GroundStrokeAnalysis:

    """analyzes only forehands/backhands"""

    def __init__(self, shot_type: str, pro_for_comparison: str):
        if shot_type not in ["forehand", "backhand"]:
            raise ValueError("shot must be either 'forehand' or 'backhand'")
        self.shot_type = shot_type
        self.pro_for_comparison_video = f"pro_videos/tennis/{self.shot_type}/{pro_for_comparison}.mp4"
        self.yolo = YOLO("sports/tennis/models/yolo11n.pt")

        # landmarker config
        BaseOptions = mp.tasks.BaseOptions
        self.PoseLandmarker = mp.tasks.vision.PoseLandmarker
        PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions
        VisionRunningMode = mp.tasks.vision.RunningMode

        self.options = PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path='sports/tennis/models/pose_landmarker_full.task'),
            running_mode=VisionRunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_segmentation_masks=False,
        )

        self.meters_per_pixel = None

    def convert_landmarks(self, pose):

        """converts a single mediapipe PoseLandmarkerResult into a (33, 3) landmark array"""

        if pose is None or not pose.pose_landmarks:
            return None

        landmark_list = pose.pose_landmarks[0]  # num_poses=1, so only one pose is expected
        landmarks_array = np.array([
            [lm.x, lm.y, lm.z] for lm in landmark_list
        ], dtype=np.float32)

        return landmarks_array

    def calculate_angle(self, a, b, c):
        ba = a - b
        bc = c - b
        denom = (np.linalg.norm(ba) * np.linalg.norm(bc)) + 1e-8
        cosang = np.dot(ba, bc) / denom
        angle = np.arccos(np.clip(cosang, -1.0, 1.0))
        return np.degrees(angle)
    
    def calculate_meters_per_pixel(self, player_height_p, player_height_m=1.7018):
        self.meters_per_pixel = player_height_m / player_height_p

    def analyze_shot(self, path):
        
        """works for both player and pro"""

        self.meters_per_pixel = None

        # read video
        cap = cv.VideoCapture(path)

        wrist_pos_buff_velocity = [] # stores positions of wrist during the shot
        joint_angle_buff = [] # stores angle dictionaries for easy comparison
        global_wrist_pos = [] # stores position of wrist during the shot as global coordinates
        player_box_history = [] # stores coordinates of player box over frames
        
        cropped_person = None
        result = None
        self.global_frame_index = 0

        # player analysis
        with self.PoseLandmarker.create_from_options(self.options) as landmarker:
            while True:
                ret, frame = cap.read()
                if not ret: break
                cropped_person = None
                result = None
                frame = cv.resize(frame, (640, 360))

                # videos are extremely short as strokes are quick
                # no throttling needed
                player_box = self.yolo.predict(
                    source=frame,
                    conf=0.2,
                    stream=False,
                    verbose=False,
                )[0].boxes

                if not player_box:
                    pass
                
                else:
                    # take largest box
                    largest_area = 0
                    best_box = None
                    for box in player_box:
                        x1, y1, x2, y2 = box.xyxy[0]
                        area = abs(x2-x1) * abs(y2-y1)
                        if area > largest_area:
                            best_box = box
                            largest_area = area
                    
                    x1, y1, x2, y2 = map(int, best_box.xyxy[0])
                    player_box_history.append((x1,y1,x2,y2))

                    cropped_person = frame[y1:y2, x1:x2]

                    if self.meters_per_pixel is None:
                        self.calculate_meters_per_pixel(player_height_p=(y2-y1))

                if cropped_person is None or cropped_person.size == 0:
                    pass
                else:
                    # use mediapipe on localized player
                    rgb_frame = cv.cvtColor(cropped_person, cv.COLOR_BGR2RGB)
                    mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
                    fps = cap.get(cv.CAP_PROP_FPS)
                    timestamp = int((self.global_frame_index / fps) * 1000)
                    result = landmarker.detect_for_video(mp_img, timestamp)

                if result is None or not result.pose_landmarks:
                    pass
                
                else:
                    landmark_arr = self.convert_landmarks(result) # convert to array
                    # crop dimensions
                    crop_h, crop_w = cropped_person.shape[:2]

                    local_wrist = np.array([
                        landmark_arr[16][0] * crop_w,
                        landmark_arr[16][1] * crop_h,
                    ])
                    wrist_pos_buff_velocity.append(local_wrist) # right wrist position

                    global_landmarks = landmark_arr.copy()

                    # convert normalized crop coordinates tp frame coordinates
                    global_landmarks[:, 0] = x1 + (global_landmarks[:, 0] * crop_w)
                    global_landmarks[:, 1] = y1 + (global_landmarks[:, 1] * crop_h)

                    # store right wrist position (pixel coordinates)
                    global_wrist_pos.append(global_landmarks[16][:2])

                    angles = {
                        "right_elbow": self.calculate_angle(
                            landmark_arr[12], # shoulder
                            landmark_arr[14], # elbow
                            landmark_arr[16], # wrist
                        ),

                        "right_shoulder": self.calculate_angle(
                            landmark_arr[24], # hip
                            landmark_arr[12], # shoulder
                            landmark_arr[14], # elbow
                        ),

                        "right_knee": self.calculate_angle(
                            landmark_arr[24], # hip
                            landmark_arr[26], # knee
                            landmark_arr[28], # ankle
                        ),

                        "left_elbow": self.calculate_angle(
                            landmark_arr[11],
                            landmark_arr[13],
                            landmark_arr[15],
                        ),

                        "left_shoulder": self.calculate_angle(
                            landmark_arr[23],
                            landmark_arr[11],
                            landmark_arr[13],
                        ),

                        "left_knee": self.calculate_angle(
                            landmark_arr[23],
                            landmark_arr[25],
                            landmark_arr[27],
                        ),
                    }

                    joint_angle_buff.append(angles)

                self.global_frame_index += 1

        cap.release()

        # joint angle comparisons done in other function
        
        positions = np.array(wrist_pos_buff_velocity)

        displacement = np.diff(
            positions,
            axis=0
        )

        distances = np.linalg.norm(
            displacement,
            axis=1
        )

        # remove pose jitter
        distances = distances[distances > 2]

        velocity = (
            distances
            * self.meters_per_pixel
            * fps
            * 2.23694
        )

        # remove impossible detections
        velocity = velocity[velocity < 100]

        velocity_output = {
            "average": float(np.mean(velocity)),
            "peak": float(np.percentile(velocity,95)),
        }

        if len(player_box_history):

            # use average player bounding box
            boxes = np.array(player_box_history)
            avg_x1, avg_y1, avg_x2, avg_y2 = np.mean(boxes, axis=0)
            player_center_x = (avg_x1 + avg_x2) / 2
            player_center_y = (avg_y1 + avg_y2) / 2
            player_width = avg_x2 - avg_x1
            player_height = avg_y2 - avg_y1
            frame_area = 640 * 360
            player_area = player_width * player_height
            player_ratio = player_area / frame_area

            target_ratio = 0.45
            scale = np.sqrt(target_ratio / max(player_ratio, 0.001))
            scale = np.clip(scale, 1.0, 3.0)

        else:

            scale = 1.0
            player_center_x = 320
            player_center_y = 180

        # wrist path

        swing_path = np.zeros((360, 640), dtype=np.float32)
        for x, y in global_wrist_pos:
            new_x = (x - player_center_x) * scale + 320
            new_y = (y - player_center_y) * scale + 180

            if 0 <= new_x < 640 and 0 <= new_y < 360:
                cv.circle(swing_path, (int(new_x), int(new_y)), 6, 255, -1)

        swing_path = swing_path.astype(np.uint8)
        swing_path = cv.cvtColor(swing_path, cv.COLOR_GRAY2BGR)
    
        return {
            "velocity": velocity_output,
            "joint_angles": joint_angle_buff,
            "swing_path": swing_path,
        }

    def run_analysis(self, player_video_path, pro_video_path):

        # analyze both strokes
        pro_results = self.analyze_shot(pro_video_path)
        player_results = self.analyze_shot(player_video_path)

        # velocity comparison
        velocity_comparison = {
            "average_difference": (
                player_results["velocity"]["average"]
                - pro_results["velocity"]["average"]
            ),

            "peak_difference": (
                player_results["velocity"]["peak"]
                - pro_results["velocity"]["peak"]
            ),

            "player": player_results["velocity"],
            "pro": pro_results["velocity"],
        }

        # joint comparison
        joint_comparison = {}

        # get all angle names
        if player_results["joint_angles"] and pro_results["joint_angles"]:

            angle_names = player_results["joint_angles"][0].keys()

            for angle in angle_names:

                player_angles = np.array([
                    frame[angle]
                    for frame in player_results["joint_angles"]
                ])

                pro_angles = np.array([
                    frame[angle]
                    for frame in pro_results["joint_angles"]
                ])

                joint_comparison[angle] = {
                    "player_average": float(np.mean(player_angles)),
                    "pro_average": float(np.mean(pro_angles)),

                    "difference": float(
                        np.mean(player_angles)
                        -
                        np.mean(pro_angles)
                    ),

                    "player_range": (
                        float(np.min(player_angles)),
                        float(np.max(player_angles))
                    ),

                    "pro_range": (
                        float(np.min(pro_angles)),
                        float(np.max(pro_angles))
                    )
                }

        # save heatmaps
        os.makedirs("frontend/heatmaps", exist_ok=True)
        id = str(uuid.uuid4()) 
        pro_output_path = f"frontend/heatmaps/{id}_pro.png"
        player_output_path = f"frontend/heatmaps/{id}_player.png"

        cv.imwrite(pro_output_path, pro_results["swing_path"])
        cv.imwrite(player_output_path, player_results["swing_path"])

        return {

            "shot_type": self.shot_type,

            "velocity": velocity_comparison,

            "joint_angles": joint_comparison,

            "visuals": {
                "player_swing_path": player_output_path,
                "pro_swing_path": pro_output_path
            }
        }