export function fifoReasonLabel(reason:string){
 const labels:Record<string,string>={shared_fifo_verified_source:'Исторический FIFO общего склада · подтверждённый источник WB',shared_fifo_manual_source:'Исторический FIFO общего склада · ручное подтверждение источника, предварительно'};
 return labels[reason]??reason.replaceAll('_',' ');
}
