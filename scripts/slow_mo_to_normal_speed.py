import cv2 as cv

def convert_slow_mo_to_normal_speed(path, output_fps):
    cap = cv.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError("invalid path")
    
    width = int(cap.get(cv.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv.CAP_PROP_FRAME_HEIGHT))

    fourcc = cv.VideoWriter_fourcc(*'mp4v')
    # out = cv.VideoWriter(path, fourcc, output_fps, (width, height))
    out = cv.VideoWriter("pro_videos/tennis/serve/test.mp4", fourcc, output_fps, (width, height))

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        out.write(frame)

    cap.release()

convert_slow_mo_to_normal_speed("pro_videos/tennis/serve/Alcaraz.mp4", 120)