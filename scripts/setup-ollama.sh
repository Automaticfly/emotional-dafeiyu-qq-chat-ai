#!/bin/bash
set -eu

cat >/usr/local/bin/ollama-warmup.sh <<'EOF'
#!/bin/bash
for _ in $(seq 1 60); do
  if curl -fsS --max-time 60 \
    http://172.17.0.1:11434/api/embed \
    -H 'Content-Type: application/json' \
    -d '{"model":"bge-m3","input":"warmup"}' >/dev/null 2>&1; then
    exit 0
  fi
  sleep 2
done
exit 0
EOF
chmod +x /usr/local/bin/ollama-warmup.sh

mkdir -p /etc/systemd/system/ollama.service.d
cat >/etc/systemd/system/ollama.service.d/override.conf <<'EOF'
[Unit]
After=docker.service
Wants=docker.service

[Service]
Environment="OLLAMA_HOST=172.17.0.1:11434"
Environment="OLLAMA_KEEP_ALIVE=-1"
Restart=always
RestartSec=2
ExecStartPost=/usr/local/bin/ollama-warmup.sh
EOF

systemctl daemon-reload
systemctl restart ollama

for _ in $(seq 1 60); do
  if curl -fsS --max-time 60 http://172.17.0.1:11434/api/tags >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo "--- service ---"
systemctl status ollama --no-pager | head -14
echo "--- listen ---"
ss -ltnp | grep 11434 || true
echo "--- model ---"
curl -fsS http://172.17.0.1:11434/api/tags | head -c 500 || true
echo
