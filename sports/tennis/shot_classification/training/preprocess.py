import cv2 as cv
import os
import mediapipe as mp
from ultralytics import YOLO
import numpy as np
from sports.tennis.shot_classification.shot_classification import ShotClassifier

labels = {
    0: "forehand",
    1: "backhand",
}

utils = ShotClassifier()

# landmarker setup
BaseOptions = mp.tasks.BaseOptions
PoseLandmarker = mp.tasks.vision.PoseLandmarker
PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode

options = PoseLandmarkerOptions(
    base_options=BaseOptions(model_asset_path='sports/tennis/models/pose_landmarker_full.task'),
    running_mode=VisionRunningMode.VIDEO,
    num_poses=1,
    min_pose_detection_confidence=0.5,
    min_pose_presence_confidence=0.5,
    min_tracking_confidence=0.5,
    output_segmentation_masks=False,
)

detector = utils.yolo

# data location
data_root = "data/shot_classification"
output_path = "sports/tennis/shot_classification/data"
strokes = ["forehand", "backhand"]

os.makedirs(output_path, exist_ok=True)

def get_best_box(frame):
    results = detector.predict(
        source=frame,
        conf=0.3,
        classes=[0],
        stream=False,
    )[0].boxes

    best_box = None
    max_area = 0
    for box in results:
        x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
        area = (x2 - x1) * (y2 - y1)
        if area > max_area:
            max_area = area
            best_box = (x1, y1, x2, y2)

    if best_box is None:
        return None

    x1, y1, x2, y2 = best_box
    box_w, box_h = x2 - x1, y2 - y1
    pad_w, pad_h = 0.35 * box_w, 0.35 * box_h
    frame_h, frame_w = frame.shape[:2]

    x1 = int(max(0, x1 - pad_w))
    y1 = int(max(0, y1 - pad_h))
    x2 = int(min(frame_w, x2 + pad_w))
    y2 = int(min(frame_h, y2 + pad_h))

    if x2 <= x1 or y2 <= y1:
        return None

    return x1, y1, x2, y2

def augment_sequence(seq, noise_std_range=(0.005, 0.03), scale_range=(0.85, 1.15),
                      time_warp_range=(0.85, 1.15), time_warp_p=0.8):
    seq = seq.copy().astype(np.float32)
    seq_len = seq.shape[0]

    if np.random.rand() < time_warp_p:
        stretch = np.random.uniform(*time_warp_range)
        warped_len = max(2, int(round(seq_len * stretch)))
        orig_idx = np.linspace(0, seq_len - 1, seq_len)
        warped_idx = np.linspace(0, seq_len - 1, warped_len)
        warped = np.stack(
            [np.interp(warped_idx, orig_idx, seq[:, d]) for d in range(seq.shape[1])],
            axis=1,
        )
        resample_idx = np.linspace(0, warped_len - 1, seq_len)
        seq = np.stack(
            [np.interp(resample_idx, np.arange(warped_len), warped[:, d]) for d in range(seq.shape[1])],
            axis=1,
        )

    scale = np.random.uniform(*scale_range)
    seq = seq * scale

    noise_std = np.random.uniform(*noise_std_range)
    seq = seq + np.random.normal(0.0, noise_std, size=seq.shape).astype(np.float32)

    return seq

def mixup_sequences(seq_a, seq_b, alpha=0.4):
    lam = np.random.beta(alpha, alpha)
    lam = max(lam, 1 - lam)  # keep the blend closer to one parent, avoid mushy 50/50 averages
    return (lam * seq_a + (1 - lam) * seq_b).astype(np.float32)

X_global, y_global = [], []  # composed of X and y locals

for stroke in strokes:
    label = 0 if stroke == "forehand" else 1

    for video in os.listdir(os.path.join(data_root, stroke)):
        if video == ".DS_Store":
            continue

        X_local, y_local = [], []

        video_path = os.path.join(data_root, stroke, video)
        cap = cv.VideoCapture(video_path)
        fps = cap.get(cv.CAP_PROP_FPS) or 30.0
        prev_pose_frame = None

        frame_idx = 0
        with PoseLandmarker.create_from_options(options) as landmarker:
            buffer = []

            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                frame = cv.resize(frame, (640, 360))

                box = get_best_box(frame)
                if box is None:

                    frame_idx += 1
                    continue

                x1, y1, x2, y2 = box
                cropped_person = frame[y1:y2, x1:x2]

                rgb_frame = cv.cvtColor(cropped_person, cv.COLOR_BGR2RGB)
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
                timestamp = int((frame_idx / fps) * 1000)
                result = landmarker.detect_for_video(mp_img, timestamp)

                landmark_arr = utils.convert_landmarks(result)
                if landmark_arr is None:
                    frame_idx += 1
                    continue
                
                if prev_pose_frame is None:
                    prev_pose_frame = landmark_arr
                    frame_idx += 1
                    continue
                features = utils.extract_features(landmark_arr, prev_pose_frame)
                prev_pose_frame = landmark_arr
                buffer.append(features)
                frame_idx += 1

                if len(buffer) == utils.seq_len:
                    X_local.append(np.array(buffer))
                    y_local.append(label)
                    buffer = []

        cap.release()

        if len(X_local) == 0:
            continue

        X_local = np.array(X_local)
        y_local = np.array(y_local)
        X_global.append(X_local)
        y_global.append(y_local)

X_all, y_all = [], []

for stroke in strokes:
    label = 0 if stroke == "forehand" else 1

    stroke_X = [X for X, y in zip(X_global, y_global) if len(y) and y[0] == label]
    stroke_y = [y for y in y_global if len(y) and y[0] == label]

    if not stroke_X:
        print(f"no samples collected for {stroke}, skipping.")
        continue

    X_stroke = np.concatenate(stroke_X, axis=0)
    y_stroke = np.concatenate(stroke_y, axis=0)

    TARGET_PER_CLASS = 2500
    MIXUP_PROB = 0.35

    n_orig = X_stroke.shape[0]
    n_needed = max(0, TARGET_PER_CLASS - n_orig)

    aug_X = []
    for i in range(n_needed):
        base_idx = i % n_orig  

        if n_orig > 1 and np.random.rand() < MIXUP_PROB:
            partner_idx = base_idx
            while partner_idx == base_idx:
                partner_idx = np.random.randint(n_orig)
            seq = mixup_sequences(X_stroke[base_idx], X_stroke[partner_idx])
        else:
            seq = X_stroke[base_idx]

        seq = augment_sequence(seq)  # always apply jitter/scale/time-warp on top
        aug_X.append(seq)

    if aug_X:
        X_stroke_aug = np.concatenate([X_stroke, np.array(aug_X)], axis=0)
        y_stroke_aug = np.concatenate([y_stroke, np.full(len(aug_X), label, dtype=y_stroke.dtype)], axis=0)
    else:
        X_stroke_aug, y_stroke_aug = X_stroke, y_stroke

    print(f"{stroke}: {X_stroke_aug.shape[0]} samples "
          f"({n_orig} original + {len(aug_X)} augmented) "
          f"of shape {X_stroke_aug.shape[1:]}")

    X_all.append(X_stroke_aug)
    y_all.append(y_stroke_aug)

if not X_all:
    print("no samples collected for any stroke, nothing saved.")
else:
    X_combined = np.concatenate(X_all, axis=0)
    y_combined = np.concatenate(y_all, axis=0)

    # shuffle so classes are interleaved rather than stroke-then-stroke
    perm = np.random.permutation(X_combined.shape[0])
    X_combined = X_combined[perm]
    y_combined = y_combined[perm]

    np.save(os.path.join(output_path, "X.npy"), X_combined)
    np.save(os.path.join(output_path, "y.npy"), y_combined)

    print(f"saved combined dataset: {X_combined.shape[0]} samples, "
          f"shape {X_combined.shape[1:]} -> X.npy, y.npy")