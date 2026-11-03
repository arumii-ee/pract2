/******************************************************************************
* Copyright (C) 2002 - 2020 Xilinx, Inc.  All rights reserved.
* SPDX-License-Identifier: MIT
******************************************************************************/
#include "xgpio.h"
/****************************************************************************/
/**
* Initialize the XGpio instance provided by the caller based on the
* given DeviceID.
*
* @param	InstancePtr is a pointer to an XGpio instance.
* @return	XST_SUCCESS if the initialization was successful.
*****************************************************************************/
int XGpio_Initialize(XGpio * InstancePtr, u16 DeviceId)
{
	XGpio_Config *ConfigPtr;
	ConfigPtr = XGpio_LookupConfig(DeviceId);
	if (ConfigPtr == (XGpio_Config *) NULL) {
		InstancePtr->IsReady = 0;
		return (XST_DEVICE_NOT_FOUND);
	}
	return XGpio_CfgInitialize(InstancePtr, ConfigPtr, ConfigPtr->BaseAddress);
}
/** Write to discrete register. */
void XGpio_DiscreteWrite(XGpio * InstancePtr, unsigned Channel, u32 Data)
{
	XGpio_WriteReg(InstancePtr->BaseAddress, 0, Data);
}
void XGpio_SetDataDirection(XGpio *InstancePtr, unsigned Channel, u32 DirectionMask) {}
