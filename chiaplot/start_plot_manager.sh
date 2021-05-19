if ps -ef | grep -v grep | grep plot_manager.py ; then
	exit 0
else
	python /home/mmv/mining/plot_manager/code/chiaplot/plot_manager.py > /home/mmv/mining/plot_manager/logs/runtime.log 2>&1
fi