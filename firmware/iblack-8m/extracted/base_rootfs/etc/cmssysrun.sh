PATH="/usr/local/bin:/usr/local/sbin:/usr/bin:/usr/sbin:/bin:/sbin:/app/nfs/bin:./"
LD_LIBRARY_PATH="/usr/local/lib:/usr/lib:/app/nfs:./"
export PATH
export LD_LIBRARY_PATH
/sbin/getty -L ttyS000 115200 vt100 -t 5&
mount -t squashfs /dev/mtdblock2 /app
if [ $? -eq 0 ]; then
/app/runapp.sh
par=$?
if [ $par -eq 2 -o $par -eq 127 -o $par -eq 251 ]; then
	sleep 2
	/usr/bin/SysUpdate 3508
fi
else
	sleep 2
	/usr/bin/SysUpdate 3508
fi
umask 022
