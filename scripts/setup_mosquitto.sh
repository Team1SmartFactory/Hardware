#!/usr/bin/env bash
# 데모 환경(우분투, FE/BE/HW를 한 머신에 로컬로 띄우는 구성) 기준 Mosquitto 설치·설정.
#
# Backend(app/mqtt/client.py)와 이 레포의 mqtt_bridge(mqtt_link.py) 둘 다 사용자/비밀번호
# 인증이나 TLS를 쓰지 않으므로, 브로커도 로컬 익명 접속 허용으로 맞춘다 — 다른 값으로
# 바꾸려면 양쪽 코드도 같이 고쳐야 한다.
#
# 사용법:
#   ./scripts/setup_mosquitto.sh        # 설치 + 설정 + 재시작 + 왕복 테스트까지 한 번에
#
# 여러 번 실행해도 안전하다(설정 파일을 덮어쓸 뿐 누적되지 않음).

set -euo pipefail

CONF_PATH="/etc/mosquitto/conf.d/smartfactory.conf"

echo "[1/4] mosquitto 설치..."
sudo apt-get update -qq
sudo apt-get install -y mosquitto mosquitto-clients

echo "[2/4] 로컬 익명 접속 허용 설정 (${CONF_PATH})..."
sudo tee "${CONF_PATH}" > /dev/null <<'EOF'
listener 1883 127.0.0.1
allow_anonymous true
EOF

echo "[3/4] mosquitto 서비스 등록 + 재시작..."
sudo systemctl enable mosquitto --quiet
sudo systemctl restart mosquitto

echo "[4/4] 왕복 테스트..."
TOPIC="smartfactory/setup-check/$$"
EXPECTED="setup-ok-$$"

TMPFILE=$(mktemp)
timeout 5 mosquitto_sub -h localhost -p 1883 -t "${TOPIC}" -C 1 > "${TMPFILE}" &
SUB_PID=$!
sleep 1  # 구독이 붙을 시간을 준다 (안 주면 publish가 구독보다 먼저 나가서 못 받음)
mosquitto_pub -h localhost -p 1883 -t "${TOPIC}" -m "${EXPECTED}"
wait "${SUB_PID}" || true
RECEIVED=$(cat "${TMPFILE}")
rm -f "${TMPFILE}"

if [ "${RECEIVED}" = "${EXPECTED}" ]; then
  echo "✅ mosquitto 정상 동작 (localhost:1883, 익명 접속 허용)"
else
  echo "❌ 왕복 테스트 실패 — 'systemctl status mosquitto'와 'journalctl -u mosquitto'로 확인할 것"
  exit 1
fi
