#!/bin/sh
# Modes: cron (default: schedule daily load+trade), trade, load, doctor, dashboard, backtest
set -e
cd /app
case "$1" in
  cron)
    # 10:45 ET weekdays: decision run (loads data first). 16:30 ET: end-of-day snapshot for the warehouse.
    printenv | grep -E '^(APCA_|OTB_|TZ=)' > /etc/environment || true
    cat > /etc/cron.d/otb <<CRON
SHELL=/bin/sh
45 10 * * 1-5 root . /etc/environment; cd /app && otb trade >> /app/state/cron.log 2>&1
30 16 * * 1-5 root . /etc/environment; cd /app && otb load >> /app/state/cron.log 2>&1
CRON
    chmod 0644 /etc/cron.d/otb; mkdir -p /app/state; touch /app/state/cron.log
    echo "otb cron installed (10:45 ET trade, 16:30 ET load). Tailing log."; cron; exec tail -f /app/state/cron.log ;;
  dashboard) exec streamlit run src/otb/monitoring/dashboard.py --server.port 8501 --server.address 0.0.0.0 -- --state /app/state ;;
  *) exec otb "$@" ;;
esac
