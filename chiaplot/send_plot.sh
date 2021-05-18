#!/bin/bash
# 
ssh pi@chiafarm01-internal -i /home/mmv/.ssh/id_rsa "nohup /home/pi/mining/plot_manager/receive_plot.sh $2 > foo.out 2> foo.err < /dev/null &"
(sudo /usr/bin/pv "$1" -f | sudo /usr/bin/nc -q 5 chiafarm01-internal 4040) > /home/mmv/mining/plot_manager/logs/runtime.log 2>&1
exit
