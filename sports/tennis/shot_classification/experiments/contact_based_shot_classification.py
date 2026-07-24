# approach: use contact detection as an indicator for when to use shot classifier
# when contact is detected, take last 40 frames and next 20 frames for a total of 60 frames

import cv2 as cv
from sports.tennis.shot_classification.shot_classification import ShotClassifier
from sports.tennis.contact_detection.contact_detection import ContactDetector
import mediapipe as mp
import numpy as np
import subprocess
import librosa

cap = cv.VideoCapture("test_videos/videoplayback10.mp4")

classifier = ShotClassifier()
detector = ContactDetector()

buffer = []
hit = False

labels = {
    0: "forehand",
    1: "backahnd",
}

frame_idx = 0
prev_pose_frame = None

# load audio
audio_path = "tmp/audio.wav"
subprocess.run([
    "ffmpeg",
    "-i", "test_videos/videoplayback11.mp4",
    "-vn",              
    "-acodec", "pcm_s16le",
    "-ar", "16000",     
    "-ac", "1", "-y",
    audio_path
], check=True) # extract audio

audio, sr = librosa.load(audio_path, sr=16_000)
detector.set_audio(audio=audio, sr=sr)

fourcc = cv.VideoWriter_fourcc(*"mp4v")
writer = cv.VideoWriter("api/runs/contact_experiment_vpb10.mp4", fourcc, 30, (640, 360))

with classifier.PoseLandmarker.create_from_options(classifier.options) as landmarker:
    while True:
        ret, frame = cap.read()
        if not ret: break
        pose_results = classifier.yolo.predict(
            source=frame,
            classes=[0],
            conf=0.3,
            stream=False,
            verbose=False,
        )

        r = pose_results[0]
        r_boxes = r.boxes

        if r_boxes is None or len(r_boxes) == 0:
            frame_idx += 1
            continue

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
        box_w, box_h = x2 - x1, y2 - y1
        pad_w, pad_h = int(0.35 * box_w), int(0.35 * box_h)

        frame_h, frame_w, _ = frame.shape
        x1 = max(0, x1 - pad_w)
        y1 = max(0, y1 - pad_h)
        x2 = min(frame_w, x2 + pad_w)
        y2 = min(frame_h, y2 + pad_h)

        cropped_person = frame[y1:y2, x1:x2]

        if cropped_person.size == 0:
            frame_idx += 1
            continue

        # landmark extraction

        rgb_frame = cv.cvtColor(cropped_person, cv.COLOR_BGR2RGB)
        mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
        timestamp_ms = int((frame_idx / 30) * 1000)
        result = landmarker.detect_for_video(mp_img, timestamp_ms)
        
        pose_frame = classifier.convert_landmarks(result)
        if pose_frame is None:
            frame_idx += 1
            continue

        feat = classifier.extract_features(pose_frame, prev_pose_frame)
        buffer.append(feat)
        prev_pose_frame = pose_frame.copy()

        # contact detection
        hit = detector.detect_contact(frame_idx=frame_idx)           

        if hit:
            buffer[-40:] # get last 40 frames to start input sequence for model
        else:
            pass

        if len(buffer) >= classifier.seq_len and hit:
            output_class, probs = classifier.process_buffer(buffer=buffer)

            display_text = output_class

            if "neutral" not in display_text:
                cv.putText(
                    frame,
                    display_text,
                    (10, 30),
                    cv.FONT_HERSHEY_SIMPLEX,
                    1,
                    (0, 255, 0),
                    2,
                    cv.LINE_AA,
                )

            # reset state vars
            buffer = []
            hit = False

        writer.write(frame)
        frame_idx += 1