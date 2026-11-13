#! /bin/sh

cmscheck()
{
	par=$1
	if [ $par -eq 2 -o $par -eq 127 -o $par -eq 251 ]; then
		exit 251
	fi
}

echo 0 > /proc/sys/net/ipv4/tcp_slow_start_after_idle
cd /app/komod; ./load3518 -i ar0141
cmscheck $?
cmserr=`dmesg | grep "SQUASHFS error:"`
if [ "$cmserr" != "" ]; then
        exit 251
fi
cd /app/cmsapp; ./cmsMain.out &
cmscheck $?
sleep 3
cmserr=`dmesg | grep "SQUASHFS error:"`
if [ "$cmserr" != "" ]; then
        exit 251
fi
exit 0
