#!/usr/bin/env bash
echo Creating the virtualenv...
python3 -m venv vphoto
source vphoto/bin/activate
echo Installing requirements...
pip install -U pip
pip install -r requirements.txt

# Copy the service file
sudo cp photoviewer.service /lib/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable photoviewer

# Set up the config file
cp photo.cfg.template photo.cfg
UUID=$(cat /proc/sys/kernel/random/uuid)
echo "UUID: $UUID"
sed -i -e "s/PKID/$UUID/g" photo.cfg
echo Remember to set the frame name and description before starting the service.
#echo "Name for this photo frame: "
#read frame_name
#read -p "Description: " desc
#sed -i -e "s/FRAMENAME/$frame_name/g" photo.cfg
#sed -i -e "s/FRAMEDESC/$desc/g" photo.cfg
