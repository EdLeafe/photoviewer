#!/usr/bin/env bash
echo Creating the virtualenv...
python3 -m venv ~/venvs/photo
source ~/venvs/photo/bin/activate
echo Installing requirements...
pip install -U pip setuptools wheel
pip install -r requirements.txt

# Copy the aliases
cat photo_aliases >> ~/.bash_aliases
source ~/.bash_aliases

# Copy the autostart file
# sudo mv /etc/xdg/lxsession/LXDE-pi/autostart /etc/xdg/lxsession/LXDE-pi/ORIGautostart
# sudo cp autostart /etc/xdg/lxsession/LXDE-pi/autostart

# Copy the service file
sudo cp photoviewer.service /lib/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable photoviewer

# Create the cron jobs to run the heartbeat check script
command="# Run the heartbeat check every 10 minutes"
cat <(fgrep -i -v "$command" <(crontab -l)) <(echo "$command") | crontab -
command="cd /home/pi/projects/photoviewer; python3 check_heartbeat.py"
job="*/10 * * * * $command"
cat <(fgrep -i -v "$command" <(crontab -l)) <(echo "$job") | crontab -

# Create the cron jobs to turn the monitor on/off
command="# Turn monitor off at 11pm"
cat <(fgrep -i -v "$command" <(crontab -l)) <(echo "$command") | crontab -
command="echo 'standby 0' | /usr/bin/cec-client -s -d 1"
job="0 23 * * * $command"
cat <(fgrep -i -v "$command" <(crontab -l)) <(echo "$job") | crontab -

command="# Turn monitor on at 6:15am"
cat <(fgrep -i -v "$command" <(crontab -l)) <(echo "$command") | crontab -
command="echo 'on 0' | /usr/bin/cec-client -s -d 1"
job="15 6 * * * $command"
cat <(fgrep -i -v "$command" <(crontab -l)) <(echo "$job") | crontab -

command="# Turn off monitor after logrotate restart every Sunday"
cat <(fgrep -i -v "$command" <(crontab -l)) <(echo "$command") | crontab -
command="echo 'standby 0' | /usr/bin/cec-client -s -d 1"
job="2 0 * * 7 $command"
cat <(fgrep -i -v "$command" <(crontab -l)) <(echo "$job") | crontab -

# Set up the config file
cp photo.cfg.template photo.cfg
UUID=$(cat /proc/sys/kernel/random/uuid)
echo "UUID: $UUID"
sed -i -e "s/PKID/$UUID/g" photo.cfg
echo Remember to set the frame name and description before starting the service.
echo You will need to restart the system for the changes to take effect
#echo "Name for this photo frame: "
#read frame_name
#read -p "Description: " desc
#sed -i -e "s/FRAMENAME/$frame_name/g" photo.cfg
#sed -i -e "s/FRAMEDESC/$desc/g" photo.cfg
