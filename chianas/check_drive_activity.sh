#! /bin/bash
dstat -r -D $1 1 3 > /home/mmv/mining/plot_manager/logs/drive_monitor.iostat
