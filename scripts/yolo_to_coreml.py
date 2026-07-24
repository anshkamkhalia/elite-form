from ultralytics import YOLO

model = YOLO("sports/tennis/models/tracker.pt")

# export to coreml format for maximum speed on apple's neural engine
model.export(format="coreml", quantize=8, nms=True)
