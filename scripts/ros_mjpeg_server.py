#!/usr/bin/env python3
"""ROS 이미지 토픽들을 MJPEG HTTP 스트림으로 재서비스한다 (:8898).

대시보드 카드에 물리 카메라 대신 노드가 그린 화면을 내보낸다:
  /cam/cam-line-a.mjpg  <- /stock/debug_image (PC2 재고 모니터의 bin a-d
                           판정 오버레이, raw bgr8 -> 여기서 JPEG 인코딩)
  /cam/cam-arrival.mjpg <- /stock/arrival_image/compressed (도착 YOLO)
"""
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import rclpy
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import CompressedImage, Image

BOUNDARY = "cctvframe"
LATEST = {}
COND = threading.Condition()

ROUTES = {
    "/cam/cam-line-a.mjpg": "bins",
    "/cam/cam-arrival.mjpg": "arrival",
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        key = ROUTES.get(self.path)
        if key is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type",
                         f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        prev = None
        try:
            while True:
                with COND:
                    COND.wait_for(lambda: LATEST.get(key) is not prev,
                                  timeout=5)
                    frame = LATEST.get(key)
                if frame is None or frame is prev:
                    return
                prev = frame
                self.wfile.write(
                    b"--" + BOUNDARY.encode() + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n")
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *_):
        pass


def main():
    rclpy.init()
    node = Node("ros_mjpeg_server")

    def put(key, jpeg):
        with COND:
            LATEST[key] = jpeg
            COND.notify_all()

    def on_bins(msg):
        frame = np.frombuffer(msg.data, np.uint8).reshape(
            msg.height, msg.width, 3)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            put("bins", buf.tobytes())

    def on_arrival(msg):
        put("arrival", bytes(msg.data))

    node.create_subscription(Image, "/stock/debug_image", on_bins,
                             qos_profile_sensor_data)
    node.create_subscription(CompressedImage,
                             "/stock/arrival_image/compressed", on_arrival,
                             qos_profile_sensor_data)
    server = ThreadingHTTPServer(("0.0.0.0", 8898), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("[ros-mjpeg] :8898", ", ".join(ROUTES))
    rclpy.spin(node)


if __name__ == "__main__":
    main()
