/** Initialize the GIC from config. */
s32 XScuGic_CfgInitialize(XScuGic *InstancePtr, XScuGic_Config *ConfigPtr, u32 EffectiveAddr) { return 0; }
/** Makes the connection between the Int_Id of the interrupt source and the associated handler. */
s32 XScuGic_Connect(XScuGic *InstancePtr, u32 Int_Id, Xil_InterruptHandler Handler, void *CallBackRef) { return 0; }
/** Enables the interrupt source provided as the argument Int_Id. */
void XScuGic_Enable(XScuGic *InstancePtr, u32 Int_Id) {}
/** The IRQ interrupt handler that dispatches to connected handlers. */
void XScuGic_InterruptHandler(XScuGic *InstancePtr) {}
XScuGic_Config *XScuGic_LookupConfig(u16 DeviceId) { return 0; }
