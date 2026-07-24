# analyzes a single serve and compares it to a pro player

# features:
# compares average wrist velocity
# compares joint angles (knees, wrist, shoulder)
# comparison: map of wrist location (swing path), track using coordinates plotted of path, mediapipe to locate wrist
# comparison: toss height
# comparison: toss statistics (drift) via ball_tracker YOLO model

import cv2 as cv
import numpy as np
import mediapipe as mp
from ultralytics import YOLO
import os
import uuid

class ServeAnalysis:

    """analyzes only serves"""

    def __init__(self, pro_for_comparison: str):
        self.shot_type = "serve"
        self.pro_for_comparison_video = f"pro_videos/tennis/{self.shot_type}/{pro_for_comparison}.mp4"
        self.yolo = YOLO("sports/tennis/models/yolo11n.pt")
        self.ball_tracker = YOLO("sports/tennis/models/tracker.pt")

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

    def _split_into_tracks(self, ball_arr, max_frame_gap=5, max_pixel_jump=80):

        tracks = [[ball_arr[0]]]

        for i in range(1, len(ball_arr)):
            prev = ball_arr[i - 1]
            curr = ball_arr[i]

            frame_gap = curr[0] - prev[0]
            pixel_dist = np.linalg.norm(curr[1:] - prev[1:])

            if frame_gap <= max_frame_gap and pixel_dist <= max_pixel_jump:
                tracks[-1].append(curr)
            else:
                tracks.append([curr])

        return [np.array(t) for t in tracks]

    def _remove_static_false_positives(self, ball_arr, bin_size=10, min_occurrences=8):

        if len(ball_arr) == 0:
            return ball_arr

        xs = ball_arr[:, 1]
        ys = ball_arr[:, 2]

        bin_x = np.round(xs / bin_size).astype(int)
        bin_y = np.round(ys / bin_size).astype(int)
        bins = list(zip(bin_x.tolist(), bin_y.tolist()))

        counts = {}
        for b in bins:
            counts[b] = counts.get(b, 0) + 1

        keep_mask = np.array([counts[b] < min_occurrences for b in bins])
        removed = int(np.count_nonzero(~keep_mask))

        if removed:
            offending_bins = sorted(
                {b: c for b, c in counts.items() if c >= min_occurrences}.items(),
                key=lambda kv: -kv[1]
            )
        return ball_arr[keep_mask]

    def calculate_toss_stats(self, ball_positions):

        empty_result = {
            "toss_drift_ft": None,
        }

        print(f"[toss debug] total ball detections: {len(ball_positions)}")

        if len(ball_positions) < 2 or self.meters_per_pixel is None:
            return empty_result

        ball_arr = np.array(ball_positions, dtype=np.float32)  # columns: frame_idx, x, y
        ball_arr = ball_arr[np.argsort(ball_arr[:, 0])]  # ensure time-sorted

        ball_arr = self._remove_static_false_positives(ball_arr)

        if len(ball_arr) < 2:
            return empty_result

        tracks = self._split_into_tracks(ball_arr)

        if not tracks:
            return empty_result

        MIN_VERTICAL_RANGE_PX = 20

        candidate_tracks = [
            t for t in tracks
            if len(t) >= 2 and (t[:, 2].max() - t[:, 2].min()) >= MIN_VERTICAL_RANGE_PX
        ]

        if not candidate_tracks:
            return empty_result

        best_track = max(candidate_tracks, key=len)

        last_frame_in_track = best_track[-1, 0]
        window_mask = (ball_arr[:, 0] >= last_frame_in_track - 5) & (ball_arr[:, 0] <= last_frame_in_track + 30)

        if len(best_track) < 2:
            return empty_result

        xs = best_track[:, 1]
        ys = best_track[:, 2]

        release_x = xs[0]

        apex_idx = int(np.argmin(ys))
        apex_x = xs[apex_idx]

        toss_drift_px = abs(apex_x - release_x)

        METERS_TO_FEET = 3.28084
        toss_drift_m = toss_drift_px * self.meters_per_pixel

        return {
            "toss_drift_ft": float(toss_drift_m * METERS_TO_FEET),
        }

    def analyze_shot(self, path):

        """works for both player and pro"""

        self.meters_per_pixel = None

        # read video
        cap = cv.VideoCapture(path)

        if not cap.isOpened():
            raise FileNotFoundError(
                f"could not open video at '{path}' — check the path exists and is a "
                f"readable video file"
            )

        # native resolution of this video — no resizing is applied anywhere
        # below, so this is the actual frame size everything is computed in
        frame_w = int(cap.get(cv.CAP_PROP_FRAME_WIDTH))
        frame_h = int(cap.get(cv.CAP_PROP_FRAME_HEIGHT))

        wrist_pos_buff_velocity = [] # stores positions of wrist during the shot
        joint_angle_buff = [] # stores angle dictionaries for easy comparison
        global_wrist_pos = [] # stores position of wrist during the shot as global coordinates
        player_box_history = [] # stores coordinates of player box over frames
        ball_pos_buff = [] # stores (frame_idx, x, y) of tracked ball in global pixel coords

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
                # no resizing — process at native resolution

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

                    if self.meters_per_pixel is None:
                        self.calculate_meters_per_pixel(player_height_p=(y2-y1))
                    box_w = x2 - x1
                    box_h = y2 - y1
                    pad_x = int(box_w * 0.4)
                    pad_y = int(box_h * 0.4)

                    px1 = max(0, x1 - pad_x)
                    py1 = max(0, y1 - pad_y)
                    px2 = min(frame_w, x2 + pad_x)
                    py2 = min(frame_h, y2 + pad_y)

                    cropped_person = frame[py1:py2, px1:px2]
                    x1, y1 = px1, py1

                ball_box = self.ball_tracker.predict(
                    source=frame,
                    conf=0.2,
                    stream=False,
                    verbose=False,
                )[0].boxes

                if ball_box:
                    best_conf = 0.0
                    best_ball = None
                    for box in ball_box:
                        conf = float(box.conf[0])
                        if conf > best_conf:
                            best_ball = box
                            best_conf = conf

                    if best_ball is not None:
                        bx1, by1, bx2, by2 = map(int, best_ball.xyxy[0])
                        ball_center_x = (bx1 + bx2) / 2
                        ball_center_y = (by1 + by2) / 2
                        ball_pos_buff.append((
                            self.global_frame_index,
                            ball_center_x,
                            ball_center_y,
                        ))

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
                    global_wrist_this_frame = global_landmarks[16][:2]
                    global_wrist_pos.append(global_wrist_this_frame)

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

        if len(wrist_pos_buff_velocity) < 2:
            raise RuntimeError(
                f"only found {len(wrist_pos_buff_velocity)} valid wrist detection(s) in "
                f"'{path}' — need at least 2 to compute velocity"
            )

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

        toss_output = self.calculate_toss_stats(ball_pos_buff)

        # wrist path, plot raw global frame coordinates directly.
        swing_path = np.zeros((frame_h, frame_w, 3), dtype=np.uint8)  # BGR

        for x, y in global_wrist_pos:
            if 0 <= x < frame_w and 0 <= y < frame_h:
                cv.circle(swing_path, (int(x), int(y)), 4, (255, 255, 255), -1)

        return {
            "velocity": velocity_output,
            "joint_angles": joint_angle_buff,
            "swing_path": swing_path,
            "toss": toss_output,
            "frame_w": frame_w,
            "frame_h": frame_h,
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

        # toss comparison
        player_toss = player_results["toss"]
        pro_toss = pro_results["toss"]

        def _safe_diff(player_val, pro_val):
            if player_val is None or pro_val is None:
                return None
            return player_val - pro_val

        toss_comparison = {
            "drift_difference_ft": _safe_diff(
                player_toss["toss_drift_ft"], pro_toss["toss_drift_ft"]
            ),
            "player": player_toss,
            "pro": pro_toss,
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

            "toss": toss_comparison,

            "visuals": {
                "player_swing_path": player_output_path,
                "pro_swing_path": pro_output_path,
                "player_frame_w": player_results["frame_w"],
                "pro_frame_w": pro_results["frame_w"],
            }
        }