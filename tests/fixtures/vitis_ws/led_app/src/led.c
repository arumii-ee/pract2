/* led.c: AXI GPIO에 연결된 LED 제어 모듈 */
#include "led.h"

static u32 led_value = 0;

/* GPIO를 초기화하고 LED 채널을 출력으로 설정한다. */
int led_init(XGpio *gpio, u16 dev_id)
{
    int status = XGpio_Initialize(gpio, dev_id);
    if (status != XST_SUCCESS) {
        return XST_FAILURE;
    }
    XGpio_SetDataDirection(gpio, LED_CHANNEL, 0x0);
    return XST_SUCCESS;
}

// LED 출력을 반전한다
void led_toggle(XGpio *gpio)
{
    led_value = ~led_value;
    XGpio_DiscreteWrite(gpio, LED_CHANNEL, led_value);
}
