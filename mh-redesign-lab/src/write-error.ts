export async function writeError(response:Response):Promise<Error>{
 let detail:unknown;
 try{detail=(await response.json())?.detail;}catch{/* Failed non-JSON responses contain no trusted explanation. */}
 if(typeof detail==='string'&&detail.length<=600)return new Error(detail);
 if(detail&&typeof detail==='object'&&!Array.isArray(detail)){
  const message=(detail as {message?:unknown}).message;
  if(typeof message==='string'&&message.length<=600)return new Error(message+' Введённые значения сохранены; повторите действие после завершения обновления.');
 }
 return new Error(response.status===422?'Проверьте заполненные поля. Изменение не сохранено.':`Изменение не сохранено (код ${response.status}). Введённые значения сохранены для повторного действия.`);
}
