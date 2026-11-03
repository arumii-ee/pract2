#ifndef XGPIO_H
#define XGPIO_H
#include "xil_types.h"
typedef struct { u32 BaseAddress; u32 IsReady; } XGpio;
int XGpio_Initialize(XGpio *InstancePtr, u16 DeviceId);
void XGpio_SetDataDirection(XGpio *InstancePtr, unsigned Channel, u32 DirectionMask);
void XGpio_DiscreteWrite(XGpio *InstancePtr, unsigned Channel, u32 Mask);
u32 XGpio_DiscreteRead(XGpio *InstancePtr, unsigned Channel);
#endif
