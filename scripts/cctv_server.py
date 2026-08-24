#!/usr/bin/env python3
"""USB 카메라를 대시보드가 볼 수 있는 MJPEG HTTP 스트림으로 내보낸다.

Backend config/registry.yaml의 카메라 streamUrl이 가리키는 실물 서버다.
브라우저가 RTSP를 못 여는 것과 같은 이유로 거창한 스트리밍 스택 대신 MJPEG을
쓴다: multipart/x-mixed-replace 응답 하나면 <img> 태그가 그대로 실시간
영상이 되고, 의존성은 OpenCV뿐이다. (주의: MJPEG은 <video> 태그로는 재생되지
않는다 — 프론트 CameraFeed가 .mjpg 주소를 <img>로 렌더한다.)

실행 (PC1, 카메라가 꽂힌 호스트):
    python3 scripts/cctv_server.py --camera cam-overview=/dev/video0
    python3 scripts/cctv_server.py \
        --camera cam-overview=/dev/video0 --camera cam-line-a=/dev/video2 \
        --port 8899 --fps 15

확인:
    curl -sN http://localhost:8899/cam/cam-overview.mjpg | head -c 400
    (--frame 경계와 JPEG 헤더가 보이면 정상. 브라우저로 열어도 된다.)

카메라 id는 registry.yaml cameras의 cameraId와 맞춘다. 같은 장치를 ROS
usb_cam 노드가 이미 잡고 있으면 열리지 않는다 — 이 서버와 ROS 카메라 노드는
같은 장치를 동시에 쓸 수 없으니 배선표에서 장치를 나눠라.
"""

from __future__ import annotations

import argparse
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

BOUNDARY = "cctvframe"


class CameraWorker(threading.Thread):
    """장치 하나를 붙잡고 최신 프레임의 JPEG 바이트를 유지한다.

    클라이언트 수와 무관하게 캡처는 이 스레드 하나만 한다 - V4L2 장치는
    이중으로 열 수 없으므로, 접속마다 여는 구조는 두 번째 시청자부터 깨진다.
    """

    def __init__(self, camera_id: str, device: str, fps: float, jpeg_quality: int) -> None:
        super().__init__(daemon=True, name=f"capture-{camera_id}")
        self.camera_id = camera_id
        self.device = device
        self.interval = 1.0 / fps
        self.jpeg_quality = jpeg_quality
        self._frame: bytes | None = None
        self._cond = threading.Condition()

    def run(self) -> None:
        while True:
            cap = cv2.VideoCapture(self.device)
            if not cap.isOpened():
                print(f"[cctv] {self.camera_id}: {self.device} 열기 실패 - 5초 후 재시도"
                      " (ROS usb_cam이 잡고 있지 않은지 확인)")
                time.sleep(5)
                continue
            print(f"[cctv] {self.camera_id}: {self.device} 캡처 시작")
            while True:
                ok, frame = cap.read()
                if not ok:
                    print(f"[cctv] {self.camera_id}: 프레임 읽기 실패 - 장치를 다시 연다")
                    break
                ok, buf = cv2.imencode(
                    ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
                if ok:
                    with self._cond:
                        self._frame = buf.tobytes()
                        self._cond.notify_all()
                time.sleep(self.interval)
            cap.release()

    def next_frame(self, previous: bytes | None, timeout: float = 5.0) -> bytes | None:
        """previous와 다른 프레임이 나올 때까지 기다렸다가 돌려준다."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._frame is None or self._frame is previous:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not self._cond.wait(remaining):
                    return None
            return self._frame


WORKERS: dict[str, CameraWorker] = {}


class StreamHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - http.server의 이름
        if self.path in ("/", "/index.html"):
            body = "\n".join(
                f"/cam/{camera_id}.mjpg ({worker.device})"
                for camera_id, worker in WORKERS.items()
            ).encode() + b"\n"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not (self.path.startswith("/cam/") and self.path.endswith(".mjpg")):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        camera_id = self.path[len("/cam/"):-len(".mjpg")]
        worker = WORKERS.get(camera_id)
        if worker is None:
            self.send_error(HTTPStatus.NOT_FOUND, f"no camera {camera_id!r}")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type",
                         f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        self.send_header("Cache-Control", "no-store")
        # 대시보드(다른 origin의 dev 서버)에서 <img>로 읽는 걸 막지 않는다.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        frame: bytes | None = None
        try:
            while True:
                frame = worker.next_frame(previous=frame)
                if frame is None:
                    return  # 카메라가 5초째 조용하다 - 연결을 끊어 재시도를 유도
                self.wfile.write(
                    b"--" + BOUNDARY.encode() + b"\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n")
                self.wfile.write(frame)
                self.wfile.write(b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass  # 시청자가 탭을 닫았다

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        print(f"[cctv] {self.client_address[0]} {format % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description="MJPEG CCTV 스트림 서버")
    parser.add_argument("--camera", action="append", required=True,
                        metavar="ID=DEVICE",
                        help="예: cam-overview=/dev/video0 (여러 번 지정 가능)")
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8899)
    parser.add_argument("--fps", type=float, default=15.0)
    parser.add_argument("--jpeg-quality", type=int, default=80)
    args = parser.parse_args()

    for spec in args.camera:
        camera_id, _, device = spec.partition("=")
        if not device:
            parser.error(f"--camera {spec!r}: ID=DEVICE 꼴이어야 한다")
        worker = CameraWorker(camera_id, device, args.fps, args.jpeg_quality)
        WORKERS[camera_id] = worker
        worker.start()

    server = ThreadingHTTPServer((args.bind, args.port), StreamHandler)
    urls = ", ".join(f"/cam/{cid}.mjpg" for cid in WORKERS)
    print(f"[cctv] http://{args.bind}:{args.port} 에서 {urls} 서비스 중")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[cctv] 종료")


if __name__ == "__main__":
    main()
