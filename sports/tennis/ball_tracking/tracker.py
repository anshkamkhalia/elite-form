import cv2 as cv
from ultralytics import YOLO
import numpy as np
import torch
import gc

class Tracker:
    def __init__(self):
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        
        self.model = YOLO("sports/tennis/models/tracker.pt").to(self.device)
        
        self.ball_trail_length = 40
        self.window_length = 25
        self.polyorder_savgol = 2
        self.centers = []
        self.dimensions = []

    def track(self, frame):
        frame = cv.resize(frame, (640, 360))
        
        track_generator = self.model.track(
            source=frame, 
            persist=True, 
            conf=0.2, 
            verbose=False, 
            tracker="sports/tennis/ball_tracking/optimized_tracker.yaml", 
            device=self.device,       
            stream=True               
        )
        
        result = next(track_generator)
        boxes = result.boxes
        
        if len(boxes.xyxy) == 0:
            del result
            return frame, None
            
        x1, y1, x2, y2 = map(int, boxes.xyxy.cpu().numpy()[0])
        
        cv.rectangle(frame, (x1, y1), (x2, y2), color=(0, 255, 0), thickness=2)
        

        del result
        del boxes
        gc.collect()
        
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
            
        return frame, (x1, y1, x2, y2)
