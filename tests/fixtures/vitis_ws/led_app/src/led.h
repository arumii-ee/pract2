#ifndef LED_H
#define LED_H
#include "xgpio.h"
#define LED_CHANNEL 1
typedef enum { LED_OFF = 0, LED_ON } led_state_t;
int  led_init(XGpio *gpio, u16 dev_id);
void led_toggle(XGpio *gpio);
#endif
