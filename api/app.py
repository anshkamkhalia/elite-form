# backend

import os
import secrets
from datetime import timedelta

import cv2 as cv
from dotenv import load_dotenv
from flask import Flask, request, jsonify, session
from werkzeug.utils import secure_filename
import librosa
import subprocess
from tqdm import tqdm

load_dotenv()

# tennis video analysis
from sports.tennis.shot_classification.shot_classification import ShotClassifier
from sports.tennis.contact_detection.contact_detection import ContactDetector
from sports.tennis.ball_tracking.tracker import Tracker
from sports.tennis.net_clearance.clearance import NetClearance

# tennis shot analysis
from sports.tennis.shot_analysis.groundstroke_analysis import GroundStrokeAnalysis
from sports.tennis.shot_analysis.serve_analysis import ServeAnalysis

from api.r2 import upload_video, presigned_video_url
from api.coaching import generate_coaching_tips
from api.db import init_app as init_db
from api.auth import auth_bp, login_required
from api.history import history_bp, save_analysis

# setup
app = Flask(__name__)

app.secret_key = os.environ.get("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax", 
    SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
    MAX_CONTENT_LENGTH=500 * 1024 * 1024,  # cap uploads at 500 MB
)

init_db(app)
app.register_blueprint(auth_bp)
app.register_blueprint(history_bp)

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

UPLOAD_DIR = "tmp"
os.makedirs(UPLOAD_DIR, exist_ok=True)

PRO_VIDEOS_ROOT = "pro_videos/tennis"


def list_pro_clips():
    clips = {}
    for shot_type in ("forehand", "backhand", "serve"):
        dir_path = os.path.join(PRO_VIDEOS_ROOT, shot_type)
        names = []
        if os.path.isdir(dir_path):
            for fname in os.listdir(dir_path):
                if fname.lower().endswith(".mp4"):
                    names.append(os.path.splitext(fname)[0])
        clips[shot_type] = sorted(names)
    return clips

@app.route("/process-tennis-video", methods=["POST"])
@login_required
def process_tennis_video():

    """
    accepts a video, runs shot classification only, writes the
    prediction onto each frame, and saves the output to /runs
    """

    classifier = ShotClassifier()
    contact_detector = ContactDetector()
    tracker = Tracker()
    clearance = NetClearance()

    # shot occurences
    occurences = {
        "forehand": 0,
        "backhand": 0,
    }
    n_sc_inferences = 0 # amount of times that the shot classifier ran, used in percent calculations

    if "video" not in request.files:
        return jsonify({"error": "no video file provided (expected form field 'video')"}), 400

    video_file = request.files["video"]

    if video_file.filename == "":
        return jsonify({"error": "empty filename"}), 400

    filename = secure_filename(video_file.filename)
    input_path = os.path.join(UPLOAD_DIR, filename)
    video_file.save(input_path)

    landmarker = classifier.PoseLandmarker.create_from_options(classifier.options)

    cap = cv.VideoCapture(input_path)
    fps = cap.get(cv.CAP_PROP_FPS) or 30
    total_frames = int(cap.get(cv.CAP_PROP_FRAME_COUNT))
    frame_size = (640, 360)

    output_filename = f"{os.path.splitext(filename)[0]}_output.mp4"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    fourcc = cv.VideoWriter_fourcc(*"mp4v")
    writer = cv.VideoWriter(output_path, fourcc, fps, frame_size)

    frame_idx = 0
    n_contacts = 0

    # load audio
    audio_path = "tmp/audio.wav"
    subprocess.run([
        "ffmpeg",
        "-i", input_path,
        "-vn",              
        "-acodec", "pcm_s16le",
        "-ar", "16000",     
        "-ac", "1", "-y",
        audio_path
    ], check=True) # extract audio

    audio, sr = librosa.load(audio_path, sr=16_000)
    contact_detector.set_audio(audio=audio, sr=sr)

    with tqdm(total=total_frames, unit="frame", desc="Processing") as pbar:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame = cv.resize(frame, frame_size)
            orig_frame = frame.copy()
            
            output_class, probs = classifier.classify_shots(
                frame=frame,
                frame_idx=frame_idx,
                landmarker=landmarker,
            )

            if output_class is not None:
                occurences[output_class] += 1 # increment if class if predicted
                n_sc_inferences += 1
            else: pass

            if frame_idx - classifier.last_pred_frame <= 40:
                display_text = classifier.previous_prediction
            else:
                display_text = "neutral"

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

            # contact detection
            contact = contact_detector.detect_contact(frame_idx=frame_idx)
            n_contacts += 1 if contact else 0

            # ball tracking
            frame, coordinates = tracker.track(frame=frame)

            if clearance.net is None:
                clearance.locate_net(orig_frame)
            else: pass
            if clearance.meters_per_pixel is None:
                clearance.calculate_meters_per_pixel(player_height_pixel=classifier.player_height_pixels)
            else: pass

            if coordinates:
                cx, cy = (coordinates[0] + coordinates[2]) // 2, (coordinates[1] + coordinates[3]) // 2

                clearance.calculate_net_clearance(ball_cx=cx, ball_cy=cy)

            writer.write(frame)

            frame_idx += 1
            pbar.update(1)
            pbar.set_description(f"Frame {frame_idx}/{total_frames}")

    cap.release()
    writer.release()

    avg_clearance = clearance.get_final_clearance()

    # calculate shot percentages
    fh_percent = (occurences["forehand"] / n_sc_inferences) * 100
    bh_percent = (occurences["backhand"] / n_sc_inferences) * 100
    h264_path = output_path.replace(".mp4", "_h264.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-i", output_path, "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-movflags", "+faststart", h264_path],
        check=True, capture_output=True,
    )

    # save output video
    key = upload_video(local_path=h264_path, folder="process_tennis_video")
    os.remove(h264_path)

    # clean temp files
    os.remove(input_path)
    os.remove(audio_path)
    os.remove(output_path)

    payload = {
        "net_clearance": avg_clearance,
        "n_contacts": n_contacts,
        "fh_percent": fh_percent,
        "bh_percent": bh_percent,
        "key": key,
        # R2's S3 endpoint rejects unsigned requests, so hand the browser a
        # presigned URL it can actually play.
        "video_url": presigned_video_url(key),
    }

    save_analysis(
        session["user_id"],
        "session",
        payload,
        original_filename=filename,
        video_key=key,
        summary={
            "net_clearance": avg_clearance,
            "n_contacts": n_contacts,
            "fh_percent": fh_percent,
            "bh_percent": bh_percent,
        },
    )

    return jsonify(payload)



@app.route("/pro-clips", methods=["GET"])
@login_required
def pro_clips():
    return jsonify(list_pro_clips())


@app.route("/process-tennis-shot-analysis", methods=["POST"])
@login_required
def process_tennis_shot_analysis():

    shot_type = request.form.get("shot_type")
    comparison_pro = request.form.get("comparison_pro")

    available = list_pro_clips()
    if shot_type not in available:
        return jsonify({"error": f"invalid shot_type '{shot_type}'"}), 400
    if comparison_pro not in available[shot_type]:
        return jsonify({"error": f"invalid comparison_pro '{comparison_pro}' for shot_type '{shot_type}'"}), 400

    if "video" not in request.files:
        return jsonify({"error": "no video file provided (expected form field 'video')"}), 400

    video_file = request.files["video"]

    if video_file.filename == "":
        return jsonify({"error": "empty filename"}), 400

    filename = secure_filename(video_file.filename)
    input_path = os.path.join(UPLOAD_DIR, filename)
    video_file.save(input_path)
    key = upload_video(local_path=input_path, folder="process_tennis_shot_analysis")

    if shot_type in ["forehand", "backhand"]:
        gr_analysis = GroundStrokeAnalysis(shot_type=shot_type, pro_for_comparison=comparison_pro)
        results = gr_analysis.run_analysis(player_video_path=input_path, pro_video_path=f"pro_videos/tennis/{shot_type}/{comparison_pro}.mp4")
    else:
        serve_analysis = ServeAnalysis(pro_for_comparison=comparison_pro)
        results = serve_analysis.run_analysis(player_video_path=input_path, pro_video_path=f"pro_videos/tennis/{shot_type}/{comparison_pro}.mp4")

    os.remove(input_path)

    payload = {
        "results": results,
        "key": key,
        "video_url": presigned_video_url(key),
    }

    velocity = (results or {}).get("velocity", {})
    save_analysis(
        session["user_id"],
        "comparison",
        payload,
        original_filename=filename,
        video_key=key,
        shot_type=shot_type,
        comparison_pro=comparison_pro,
        summary={
            "avg_velocity_diff": velocity.get("average_difference"),
            "peak_velocity_diff": velocity.get("peak_difference"),
        },
    )

    return jsonify(payload), 200


@app.route("/coaching-tips", methods=["POST"])
@login_required
def coaching_tips():

    payload = request.get_json(silent=True)
    if not payload or "results" not in payload:
        return jsonify({"error": "expected JSON body with a 'results' field"}), 400

    try:
        coaching = generate_coaching_tips(
            results=payload["results"],
            shot_type=payload.get("shot_type", "shot"),
            comparison_pro=payload.get("comparison_pro", "a professional"),
        )
        return jsonify(coaching), 200
    except Exception as e:
        print(e)
        return jsonify({"error": f"coaching generation failed: {e}"}), 502


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)