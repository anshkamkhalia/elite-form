# shot classification feature class

import tensorflow as tf
import numpy as np
import cv2 as cv
from ultralytics import YOLO
import mediapipe as mp
from typing import List, Optional

# load model classes (change to functional later?)
from sports.tennis.shot_classification import sc_model

class ShotClassifier:

    def __init__(self):

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

        self.player_height_pixels = None

        # model loading
        self.shot_classifier = tf.keras.models.load_model(
            "sports/tennis/models/binary_shot_classifier.keras",
            custom_objects={"ShotClassifier": sc_model.ShotClassifier}
        )
        self.yolo = YOLO("sports/tennis/models/yolo11n.pt")

        # configs
        self.seq_len = 60
        self.neutral_threshold = 0.8
        self.slide_step = 30  # how far to slide the buffer after a prediction
        self.labels = {
            0: "forehand",
            1: "backhand",
        }

        # state vars
        self.prev_pose = None # stores previous yolo results (for the every-3-frame throttle)
        self.prev_landmarks_mp_result = None # stores previous raw mp PoseLandmarkerResult
        self.prev_pose_frame = None # stores previous frame's raw (33,3) landmark array
        self.shot_buffer = [] # stores extracted feature vectors for model input
        self.previous_prediction = "neutral"
        self.last_pred_frame = -float('inf')

    def convert_landmarks(self, pose) -> Optional[np.ndarray]:

        """converts a single mediapipe PoseLandmarkerResult into a (33, 3) landmark array"""

        if pose is None or not pose.pose_landmarks:
            return None

        landmark_list = pose.pose_landmarks[0]  # num_poses=1, so only one pose is expected
        landmarks_array = np.array([
            [lm.x, lm.y, lm.z] for lm in landmark_list
        ], dtype=np.float32)

        return landmarks_array

    def calculate_angle(self, a, b, c):

        """calculates the angle between a,b,c"""

        ba = a - b
        bc = c - b
        denom = (np.linalg.norm(ba) * np.linalg.norm(bc)) + 1e-8
        cosang = np.dot(ba, bc) / denom
        return np.arccos(np.clip(cosang, -1.0, 1.0))

    def extract_features(self, pose_frame, prev_pose_frame):

        """extracts extra shot classification features from poses"""

        features = []

        left_hip = pose_frame[23]
        right_hip = pose_frame[24]
        left_shoulder = pose_frame[11]
        right_shoulder = pose_frame[12]

        hip_center = (left_hip + right_hip) / 2.0
        shoulder_center = (left_shoulder + right_shoulder) / 2.0

        torso = np.linalg.norm(shoulder_center - hip_center)
        torso = torso if torso > 1e-6 else 1.0

        normalized_pose = (pose_frame - hip_center) / torso
        features.extend(normalized_pose.flatten())

        if prev_pose_frame is None:
            velocity = np.zeros_like(normalized_pose)
        else:
            prev_left_hip = prev_pose_frame[23]
            prev_right_hip = prev_pose_frame[24]
            prev_left_shoulder = prev_pose_frame[11]
            prev_right_shoulder = prev_pose_frame[12]

            prev_hip_center = (prev_left_hip + prev_right_hip) / 2.0
            prev_shoulder_center = (prev_left_shoulder + prev_right_shoulder) / 2.0

            prev_torso = np.linalg.norm(prev_shoulder_center - prev_hip_center)
            prev_torso = prev_torso if prev_torso > 1e-6 else 1.0

            prev_normalized_pose = (prev_pose_frame - prev_hip_center) / prev_torso
            velocity = normalized_pose - prev_normalized_pose

        features.extend(velocity.flatten())

        angle_features = [
            self.calculate_angle(normalized_pose[11], normalized_pose[13], normalized_pose[15]),
            self.calculate_angle(normalized_pose[12], normalized_pose[14], normalized_pose[16]),
            self.calculate_angle(normalized_pose[23], normalized_pose[25], normalized_pose[27]),
            self.calculate_angle(normalized_pose[24], normalized_pose[26], normalized_pose[28]),
            self.calculate_angle(normalized_pose[13], normalized_pose[11], normalized_pose[23]),
            self.calculate_angle(normalized_pose[14], normalized_pose[12], normalized_pose[24]),
        ]

        features.extend(angle_features)

        right_wrist = normalized_pose[16]
        left_wrist = normalized_pose[15]

        wrist_features = [
            right_wrist[0], right_wrist[1], right_wrist[2],
            left_wrist[0], left_wrist[1], left_wrist[2],
            np.linalg.norm(velocity[16]),
            np.linalg.norm(velocity[15]),
        ]

        features.extend(wrist_features)

        return np.array(features, dtype=np.float32)

    def process_buffer(self, buffer: List) -> str:

        """takes in a buffer of >= seq_len feature frames and returns the predicted shot label"""

        output_class = None
        sequence = np.array(buffer[-self.seq_len:], dtype=np.float32)
        sequence = sequence[np.newaxis, ...]
        probs = self.shot_classifier.predict(sequence, verbose=0)[0]
        if probs < 0.5:
            output_class = "forehand"
        else:
            output_class = "backhand"

        return output_class, probs

    def classify_shots(self, frame: np.ndarray, frame_idx: int, landmarker) -> Optional[str]:

        if self.prev_pose is None or frame_idx % 1 == 0:
            self.pose_results = self.yolo.predict(
                source=frame,
                classes=[0],
                conf=0.3,
                stream=False,
                verbose=False,
            )
            self.prev_pose = self.pose_results
        else:
            self.pose_results = self.prev_pose

        r = self.pose_results[0]
        r_boxes = r.boxes

        if r_boxes is None or len(r_boxes) == 0:
            return None

        # assume that the largest person is poi (person of interest)
        best_box = None
        max_area = 0

        for box in r_boxes:
            x1, y1, x2, y2 = box.xyxy[0]
            area = (x2 - x1) * (y2 - y1)
            if area > max_area:
                max_area = area
                best_box = box

        x1, y1, x2, y2 = map(int, best_box.xyxy[0])

        if not self.player_height_pixels:
            self.player_height_pixels = abs(y2-y1)

        box_w, box_h = x2 - x1, y2 - y1
        pad_w, pad_h = int(0.35 * box_w), int(0.35 * box_h)

        frame_h, frame_w, _ = frame.shape
        x1 = max(0, x1 - pad_w)
        y1 = max(0, y1 - pad_h)
        x2 = min(frame_w, x2 + pad_w)
        y2 = min(frame_h, y2 + pad_h)

        cropped_person = frame[y1:y2, x1:x2]

        if cropped_person.size == 0:
            return None

        # landmark extraction
        if self.prev_landmarks_mp_result is None or frame_idx % 1 == 0:

            rgb_frame = cv.cvtColor(cropped_person, cv.COLOR_BGR2RGB)
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
            timestamp_ms = int((frame_idx / 30) * 1000)
            result = landmarker.detect_for_video(mp_img, timestamp_ms)

            if result is None or not result.pose_landmarks:
                result = self.prev_landmarks_mp_result
            else:
                self.prev_landmarks_mp_result = result
        else:
            result = self.prev_landmarks_mp_result

        pose_frame = self.convert_landmarks(result)

        if pose_frame is None:
            return None

        # features
        feat = self.extract_features(pose_frame, self.prev_pose_frame)
        self.shot_buffer.append(feat)
        self.prev_pose_frame = pose_frame.copy()

        # run classifier
        output_class = None
        probs = None

        if len(self.shot_buffer) >= self.seq_len:
            output_class, probs = self.process_buffer(self.shot_buffer)

            self.previous_prediction = output_class
            self.last_pred_frame = frame_idx

            # slide the buffer forward instead of clearing it entirely
            self.shot_buffer = self.shot_buffer[self.slide_step:]

        return output_class, probs if probs else -1