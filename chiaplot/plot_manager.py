#!/usr/bin/python3
# -*- coding: utf-8 -*-

__author__ = 'Richard J. Sears'
VERSION = "0.5 (2021-04-22)"

# Simple python script that helps to move my chia plots from my plotter to
# my nas. I wanted to use netcat as it was much faster on my 10GBe link than
# rsync and the servers are secure so I wrote this script to manage that
# move process.

# This is part of a two part process. On the NAS server there is drive_manager.py
# that manages the drives themselves and decides based on various criteria where
# the incoming plots will be placed. This script simply sends those plots when
# they are ready to send.

#   Updates
#
#   V0.5 2021-04-13
#   - Altered process_plot() and process_control() to integrate network
#     activity monitoring so we do not simply rely on the checkfile. This
#     now requites that Glances be installed and in API mode. We call the
#     Glances API and verify if we have network traffic on the link to the
#     NAS. If we have traffic and the checkfile exists then we know that a 
#     transfer is in progress. If we do not have any network transfer activity
#     we assume there is no transfer and we attempt a reset by removing the
#     checkfile and calling main(). As part of this update we also added 
#     a couple of functions to try and determine if Glances is running and if
#     is not, throw an error and exit. 
#
#   V0.4 2021-04-13 (bumped version to match drive_manager.py
#   - Due to issue with plot size detection happening after plot selection
#     caused an issue where plots did not get moved at all if the first selected
#     plot was the wrong size. Updated get_list_of_plots() to use pathlib to check
#     for proper filesize before passing along the plot name.
#
#   V0.2 2021-03-23
#   - Added per_plot system notification function (send_new_plot_notification()
#     in chianas drive_manager.py and updated process_plot() and verify_plot_move()
#     to support the new function
#   - Moved remote_mount lookup to happen before starting the plot move

import os
import sys
sys.path.append('/home/mmv/mining/plot_manager/code/chiaplot')
import subprocess
import logging
from system_logging import setup_logging
from system_logging import read_logging_config
import glob
import pathlib
import json
import urllib.request
import psutil
import time


# Let's do some housekeeping
nas_server = 'pi@chiafarm01-internal' # Internal 10Gbe link, entry in /etc/hosts
plot_server = 'chiaplot01'
network_interface = 'enp9s0' # Network interface (ifconfig) that plots are sent over

# Are we testing?
testing = False
if testing:
    plot_dirs = ['/Users/milko/mining/test_plot_manager/hdd1/', '/Users/milko/mining/test_plot_manager/hdd2/', '/Users/milko/mining/test_plot_manager/hdd3/', '/media/mmv/Lots_Plot1/']
    plot_size = 10000000
    status_file = '/home/mmv/mining/plot_manager/transfer_job_running_testing'
else:
    plot_dirs = ['/media/mmv/Lots_Plot1/', '/media/mmv/temp2/', '/media/mmv/Lots_Chia1/', '/media/mmv/Lots_Chia2/', '/media/mmv/Lots_Chia3/', '/media/mmv/Lots_Chia4/']
    plot_size = 108644374730  # Based on K32 plot size
    status_file = '/home/mmv/mining/plot_manager/transfer_job_running'

remote_checkfile = '/home/pi/mining/plot_manager/remote_transfer_is_active'

# Setup Module logging. Main logging is configured in system_logging.py
setup_logging()
level = read_logging_config('plot_manager_config', 'system_logging', 'log_level')
level = logging._checkLevel(level)
log = logging.getLogger(__name__)
log.setLevel(level)

# Look in our plot directory and get a list of plots. Do a basic
# size check for sanity's sake.
def get_list_of_plots():
    log.debug('get_list_of_plots() Started')

    for plot_dir in plot_dirs:
        try:
            plot_to_process = [plot for plot in pathlib.Path(plot_dir).glob("*.plot") if plot.stat().st_size > plot_size]
            log.debug(f'{plot_to_process[0].name}')
            return (plot_dir, plot_to_process[0].name)
        except IndexError:
            log.debug(f'{plot_dir} is Empty: No Plots to Process.')

    log.debug(f'All plot directories are empty. Will check again soon!')
    return (False, False)

# If we have plots and we are NOT currently transferring another plot and
# we are NOT testing the script, then process the next plot if there is
# one to process.
def process_plot():
    log.debug('process_plot() Started')
    if not process_control('check_status', 0):
        
        plot_dir, plot_to_process = get_list_of_plots()

        if plot_to_process and not testing:
            process_control('set_status', 'start')
            plot_path = plot_dir + plot_to_process
            log.info(f'Processing Plot: {plot_path}')
            try:
                remote_mount = str(subprocess.check_output(
                    ['ssh', "-i", "/home/mmv/.ssh/id_rsa", nas_server, 'grep Lots_ /home/pi/mining/plot_manager/code/chianas/plot_manager_config | awk {\'print $3\'}']).decode(('utf-8'))).strip("\n")
            except subprocess.CalledProcessError as e:
                log.warning(e.output)  # TODO Do something here...cannot go on...
                quit()
            log.debug(f'{nas_server} reports remote mount as {remote_mount}')
            subprocess.call(['/home/mmv/mining/plot_manager/code/chiaplot/send_plot.sh', plot_path, plot_to_process])
            try:
                subprocess.call(['ssh', "-i", "/home/mmv/.ssh/id_rsa", nas_server, '/home/pi/mining/plot_manager/code/chianas/kill_nc.sh'])  # make sure all of the nc processes are dead on the receiving end
                log.debug('Remote nc kill called!')
            except subprocess.CalledProcessError as e:
                log.warning(e.output)
            if verify_plot_move(remote_mount, plot_path, plot_to_process):
                log.info('Plot Sizes Match, we have a good plot move!')
            else:
                log.debug('FAILURE - Plot sizes DO NOT Match - Exiting') # ToDo Do some notification here and then...?
                process_control('set_status', 'stop') #Set to stop so it will attempt to run again in the event we want to retry....
                main() # Try Again
            process_control('set_status', 'stop')
            os.remove(plot_path)
            log.info(f'Removing: {plot_path}')
        elif testing:
            log.debug('Testing Only - Nothing will be Done!')
        else:
            return
    else:
        return

# This assumes passwordless SSH between this host and remote host.
# Make changes as necessary! Checks to make sure we are not already
# doing a file transfer. If we are we just return. If not we go ahead
# and start the process notifying this local machine as well as the
# remote NAS that a file transfer will be in progress. Right now the
# remote notification does not do anything, but I have plans to use
# it for more control so I am leaving it here.

def process_control(command, action):
    log.debug(f'process_control() called with [{command}] and [{action}]')
    if command == 'set_status':
        if action == "start":
            if os.path.isfile(status_file):
                log.debug(f'Status File: [{status_file}] already exists!')
                return
            else:
                os.open(status_file, os.O_CREAT)
                try:
                    subprocess.check_output(['ssh', "-i", "/home/mmv/.ssh/id_rsa", nas_server, 'touch %s' % remote_checkfile])
                except subprocess.CalledProcessError as e:
                    log.warning(e.output) #Nothing to add here yet as we are not using this function remotely (yet)
        if action == "stop":
            if os.path.isfile(status_file):
                os.remove(status_file)
                try:
                    subprocess.check_output(['ssh', "-i", "/home/mmv/.ssh/id_rsa", nas_server, 'rm %s' % remote_checkfile])
                except subprocess.CalledProcessError as e:
                    log.warning(e.output) #Nothing to add here yet as we are not using this function remotely (yet)
            else:
                log.debug(f'Status File: [{status_file}] does not exist!')
                return
    elif command == 'check_status':
        log.debug(f'check_status start')

        if os.path.isfile(status_file) and check_transfer():
            log.debug(f'Checkfile and Network Traffic Exists, We are currently Running a Transfer, Exiting')
            return True
        elif os.path.isfile(status_file) and not check_transfer():
            log.debug('WARNING! - Checkfile exists but there is no network traffic! Forcing Reset')
            os.remove(status_file)
            try:
                log.debug(f'executing {remote_checkfile}')
                subprocess.check_output(['ssh', "-i", "/home/mmv/.ssh/id_rsa", nas_server, 'rm %s' % remote_checkfile])
            except subprocess.CalledProcessError as e:
                log.warning(e.output)
            try:
                log.debug(f'executing kill_nc')
                subprocess.call(['ssh', "-i", "/home/mmv/.ssh/id_rsa", nas_server, '/home/pi/mining/plot_manager/code/chianas/kill_nc.sh'])  # make sure all of the nc processes are dead on the receiving end
                log.debug('Remote nc kill called!')
            except subprocess.CalledProcessError as e:
                log.warning(e.output)
            main()
        else:
            log.debug(f'Checkfile Does Not Exist and there is no network traffic!')
            return False
    else:
        return


def verify_plot_move(remote_mount, plot_path, plot_to_process):
    log.debug('verify_plot_move() Started')
    log.debug (f'Verifing: {nas_server}: {remote_mount}/{plot_to_process}')
    try:
        remote_plot_size = (int(subprocess.check_output(['ssh', "-i", "/home/mmv/.ssh/id_rsa", nas_server, 'ls -al %s | awk {\'print $5\'}' % f'{remote_mount}/{plot_to_process}'])))
    except subprocess.CalledProcessError as e:
        log.warning(e.output) #TODO Do something here...cannot go on...
        quit()
    log.debug(f'Remote Plot Size Reported as: {remote_plot_size}')
    local_plot_size = os.path.getsize(plot_path)
    log.debug(f'Local Plot Size Reported as: {local_plot_size}')
    if remote_plot_size == local_plot_size:
        try:
            subprocess.check_output(['ssh', "-i", "/home/mmv/.ssh/id_rsa", nas_server, 'touch %s' % '/home/pi/mining/plot_manager/new_plot_received'])
        except subprocess.CalledProcessError as e:
            log.warning(e.output)
        return True
    else:
        log.debug(f'Plot Size Mismatch!')
        return False

def check_transfer():
    log.debug(f'check_transfer Start')
    try:
        with urllib.request.urlopen(f"http://localhost:61208/api/3/network/interface_name/{network_interface}") as url:
            data = json.loads(url.read().decode())
            current_transfer_speed =  (data[network_interface][0]['tx']/1000000)
            if current_transfer_speed < 5:
                return False
            else:
                return True
    except urllib.error.URLError as e:
        print (e.reason)
        exit()

def checkIfProcessRunning(processName):
    '''
    Check if there is any running process that contains the given name processName.
    '''
    #Iterate over the all the running process
    for proc in psutil.process_iter():
        try:
            # Check if process name contains the given name string.
            if processName.lower() in proc.name().lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return False


def verify_glances_is_running():
    log.debug('verify_glances_is_running() Started')
    if not checkIfProcessRunning('glances'):
        log.debug('WARNING - This script requires the Glances API to operate properly.')
        log.debug('We were unable to determine if Glances is running. Please verify and try again!')
        exit()
    else:
        return True


def main():
    log.debug('Plot manager starting')
    if verify_glances_is_running():
        process_plot()
    else:
        print('Glances is Required for this script!')
        print('Please install and restart this script.')
        exit()


if __name__ == '__main__':
    main()

