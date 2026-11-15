/* Offline ARM smoke test of the trial-only logging patch. No network is used. */
#include "utils/includes.h"
#include "utils/common.h"

int main(void)
{
	wpa_msg(NULL, MSG_INFO, "CTRL-EVENT-CONNECTED - hidden=%s", "do-not-print");
	wpa_msg(NULL, MSG_INFO, "CTRL-EVENT-CONNECTED-FAKE secret=%s", "do-not-print");
	wpa_msg(NULL, MSG_INFO, "CTRL-EVENT-DISCONNECTED bssid=%s", "do-not-print");
	wpa_msg(NULL, MSG_INFO, "CTRL-EVENT-SSID-TEMP-DISABLED ssid=%s", "do-not-print");
	wpa_msg(NULL, MSG_INFO, "CTRL-EVENT-ASSOC-REJECT bssid=%s", "do-not-print");
	wpa_msg(NULL, MSG_INFO, "CTRL-EVENT-SCAN-FAILED ret=%d", -1);
	wpa_msg(NULL, MSG_INFO, "unrelated secret=%s", "do-not-print");
	return 0;
}