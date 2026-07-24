# calculates average net clearance
import cv2 as cv
import numpy as np

class NetClearance:

    def __init__(self):
        self.w = 640
        self.h = 360
        # we will assume that the net cord will always remain around the center of the frame (vertical distance)
        # tune thresholds
        self.min_net_height = self.h // 2 - 80
        self.max_net_height = self.h // 2 + 80

        self.total_iterations = 0 # amount of times net clearance was calculated
        self.running_total = 0 # running sum of calcualted net clearances, averaged in the end

        self.net = None
        self.meters_per_pixel = None

    def distance_point_to_line(self, ball_center, net_p1, net_p2):
        x0, y0 = ball_center
        x1, y1 = net_p1
        x2, y2 = net_p2
        
        A = y2 - y1
        B = x1 - x2
        C = (x2 * y1) - (x1 * y2)
        
        numerator = abs(A * x0 + B * y0 + C)
        denominator = np.sqrt(A**2 + B**2)
        
        return numerator / denominator

    def locate_net(self, frame):

        # convert to grayscale
        gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
        
        # canny edge detection
        edges = cv.Canny(gray, 500, 600, apertureSize=3)
        
        # hough transform (lines)
        lines = cv.HoughLinesP(
            edges, 
            rho=1, 
            theta=np.pi/180, 
            threshold=100, 
            minLineLength=50, 
            maxLineGap=10,
        )
        
        if lines is not None:

            # assume that the longest line is the net
            net = None

            lines = lines.reshape(-1, 4)
            
            print(len(lines))

            possible_nets = []
            
            for line in lines:
                x1, y1, x2, y2 = line
                
                if y1 >= self.min_net_height and y1 <= self.max_net_height and y2 >= self.min_net_height and y2 <= self.max_net_height: # check if net is in range
                    net = (x1, y1, x2, y2)
                    possible_nets.append(net)
                    print("new net detected")

            best_distance = 0
            self.net = None

            if not possible_nets:
                self.net = None

            for net in possible_nets:
                x1, y1, x2, y2 = net
                dist = np.sqrt((x2-x1)**2 + (y2-y1)**2)
                if dist >= best_distance:
                    self.net = net
                    best_distance = dist
                    print("found new net from candidates")

            x1, y1, x2, y2 = self.net
            # cv.line(frame, (x1, y1), (x2, y2), (0, 255, 0), 2) # debug line: draw net
        
        else:
            self.net = None

    def calculate_meters_per_pixel(self, player_height_pixel, player_height_meters=1.7018):
        self.meters_per_pixel = player_height_meters / player_height_pixel

    def calculate_net_clearance(self, ball_cx, ball_cy):
        x1, y1, x2, y2 = self.net
        net_pt1, net_pt2 = (x1, y1), (x2, y2)
        center = (ball_cx, ball_cy)
        dist = self.distance_point_to_line(center, net_pt1, net_pt2)

        # convert dist into meter conversion
        converted = dist * self.meters_per_pixel
        inch = converted * 39.3701 # convert to inches

        self.running_total += inch
        self.total_iterations += 1

    def get_final_clearance(self):
        return self.running_total/self.total_iterations